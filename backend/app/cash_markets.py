"""Fuentes y sesiones de mesas USD. Solo simulación, sin bróker ni apalancamiento.

Yahoo publica precios de referencia, no bid/ask ejecutables: spread, comisión,
deslizamiento y fracciones de acciones son hipótesis explícitas del simulador.
"""

import asyncio
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import market_data
from .config import Symbol

STOCKS=('AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AMD','AVGO','JPM',
        'V','MA','COST','WMT','XOM','UNH','NFLX','PLTR','SPY','QQQ')
FX=('EURUSD','GBPUSD','AUDUSD','NZDUSD')


def market_session(market, now=None):
    dt=datetime.fromtimestamp(time.time() if now is None else now,ZoneInfo('America/New_York'))
    minute=dt.hour*60+dt.minute
    if market=='forex':
        opened=(dt.weekday()<4 or dt.weekday()==4 and minute<17*60
                or dt.weekday()==6 and minute>=17*60)
        return dict(open=opened,label='Sesión forex 24/5' if opened else 'Forex cerrado · reapertura domingo 17:00 Nueva York')
    # La ventana real y los festivos se corroboran además con currentTradingPeriod
    # de la fuente. Una fecha no corroborada jamás autoriza entradas.
    opened=dt.weekday()<5 and 570<=minute<960
    return dict(open=opened,label='Sesión regular de EE. UU.' if opened else 'Acciones cerradas · sesión regular 09:30–16:00 Nueva York')


class CashMarketFeed:
    def __init__(self, market):
        if market not in ('forex','stocks'): raise ValueError('Mesa desconocida')
        self.market=market
        self.symbols={s:Symbol(s,s,'forex' if market=='forex' else 'acciones','yahoo',s+'=X' if market=='forex' else s)
                      for s in (FX if market=='forex' else STOCKS)}
        self.fee=.00002 if market=='forex' else .0005
        self.slippage=.00002 if market=='forex' else .0002
        self.spread=.00015 if market=='forex' else .0005
        self.scope='Forex · pares con cotización USD' if market=='forex' else 'Acciones y ETF de EE. UU. · USD'
        self.cache={}
        self.quote_per_euro=None
        self.quote_per_euro_at=None

    def session(self):
        return market_session(self.market)

    async def read(self,symbol):
        cached=self.cache.get(symbol)
        if cached and time.time()-cached[0]<30: return cached[1]
        source=symbol+'=X' if len(symbol)==6 and symbol.endswith('USD') else symbol
        response=await market_data.client().get(market_data.YAHOO_CHART.format(symbol=source),
                                               params=dict(interval='1m',range='1d',includePrePost='false'))
        response.raise_for_status()
        meta=response.json()['chart']['result'][0]['meta']
        price=float(meta['regularMarketPrice'])
        stamp=float(meta.get('regularMarketTime') or 0)
        previous=float(meta.get('chartPreviousClose') or meta.get('previousClose') or price)
        volume=float(meta.get('regularMarketVolume') or 0)*price
        if (not all(math.isfinite(v) for v in (price,stamp,previous,volume))
                or min(price,stamp,previous)<=0 or volume<0):
            raise ValueError('Referencia de mercado inválida')
        regular=meta.get('currentTradingPeriod',{}).get('regular',{})
        verified_session=(float(regular.get('start',0))<=time.time()<float(regular.get('end',0)))
        value=dict(price=price,at=stamp,change=(price/previous-1)*100,
                   volume=volume,
                   sessionVerified=verified_session,currency=meta.get('currency'))
        self.cache[symbol]=(time.time(),value)
        return value

    async def snapshot(self,symbols=None):
        keys=list(symbols or self.symbols)
        # No descargar cotizaciones de un mercado cerrado como si fueran nuevas.
        opened=self.session()['open']
        sem=asyncio.Semaphore(4)
        async def read_one(key):
            async with sem:
                try: return key,await asyncio.wait_for(self.read(key),timeout=5)
                except Exception: return key,None
        raw=dict(await asyncio.gather(*(read_one(k) for k in keys))) if opened else {}
        eur=raw.get('EURUSD')
        if opened and not eur:
            try: eur=await asyncio.wait_for(self.read('EURUSD'),timeout=5)
            except Exception: pass
        if (eur and eur.get('currency')=='USD' and math.isfinite(eur['price']) and eur['price']>0
                and 0<=time.time()-eur['at']<=120):
            self.quote_per_euro=eur['price']
            self.quote_per_euro_at=eur['at']
        data=dict(at=time.time(),metadata={},books={},tickers={},quotes=raw,session=self.session())
        for key in keys:
            quote=raw.get(key)
            valid=bool(quote and quote.get('currency')=='USD' and math.isfinite(quote['price']) and quote['price']>0
                       and -5<=time.time()-quote['at']<=120 and opened
                       and (self.market=='forex' or quote['sessionVerified']))
            data['metadata'][key]=dict(symbol=key,baseAsset=key,quoteAsset='USD',status='TRADING' if valid else 'CLOSED',
                isSpotTradingAllowed=valid,filters=[dict(filterType='LOT_SIZE',stepSize='.000001',minQty='.000001'),
                                                  dict(filterType='MIN_NOTIONAL',minNotional='1')])
            if valid:
                mid=quote['price']
                data['books'][key]=dict(bidPrice=mid*(1-self.spread/2),askPrice=mid*(1+self.spread/2),
                                       askQty=1e12,referenceAt=quote['at'],synthetic=True)
            data['tickers'][key]=dict(quoteVolume=quote['volume'] if quote else 0,
                                     priceChangePercent=quote['change'] if quote else 0)
        return data

    def quality(self,symbol,data):
        meta=data['metadata'].get(symbol,{})
        ok=bool(meta.get('status')=='TRADING' and data['session']['open'] and symbol in data['books'])
        if ok and self.market=='stocks':
            ok=float(data['tickers'][symbol]['quoteVolume'])>=10_000_000
        return dict(eligible=ok,reason='Referencia reciente; spread simulado' if ok else
                    data['session']['label'] if not data['session']['open'] else 'Precio, sesión o volumen sin corroborar')

    def universe(self,data):
        rows=[]
        for symbol in self.symbols:
            quality=self.quality(symbol,data)
            rows.append(dict(symbol=symbol,base=symbol,category=self.symbols[symbol].category,
                eligible=quality['eligible'],change24h=data['tickers'][symbol]['priceChangePercent'],
                volumeUSD=data['tickers'][symbol]['quoteVolume'],spreadBp=self.spread*10000,
                reason=quality['reason'],qualityReason=quality['reason'],
                status='pending' if quality['eligible'] else 'filtered'))
        return rows

    def wire(self,symbol,data):
        quality=self.quality(symbol,data)
        return dict(ok=quality['eligible'],symbol=symbol,source='Yahoo Finance · precios de referencia',
                    checkedAt=time.time(),socialChecked=False,syntheticSpread=True,
                    detail=quality['reason']+'; fracciones y costes hipotéticos, sin libro ejecutable')
