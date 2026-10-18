"""Revisión reproducible con datos públicos. No modifica ninguna cuenta."""
import asyncio
import json
import time
from pathlib import Path
from app import desk,market_data,opportunities
from app.desk_replay import replay

ROOT=Path(__file__).resolve().parents[1]


async def main():
    output=ROOT/'.runtime'/'review-20261018'
    output.mkdir(parents=True,exist_ok=True)
    snapshot=await opportunities.snapshot()
    candidates=[c for c in desk.universe(snapshot) if c['eligible']]
    print(json.dumps(dict(examined=len(desk.universe(snapshot)),eligible=len(candidates)),ensure_ascii=False),flush=True)
    end=int(time.time()//900)*900
    start=end-21*86400
    holdout=end-7*86400
    sem=asyncio.Semaphore(3)
    async def evaluate(candidate):
        symbol=candidate['symbol']
        path=output/(symbol+'-15m.json')
        async with sem:
            if path.exists(): rows=json.loads(path.read_text())
            else:
                rows=[];cursor=(start-250*900)*1000
                while cursor<end*1000:
                    raw=await market_data._binance_get('/api/v3/klines',dict(symbol=symbol,interval='15m',startTime=cursor,endTime=end*1000-1,limit=1000))
                    if not raw: break
                    rows.extend(dict(time=k[0]//1000,open=float(k[1]),high=float(k[2]),low=float(k[3]),close=float(k[4]),volume=float(k[5])) for k in raw)
                    cursor=raw[-1][0]+900000
                path.write_text(json.dumps(rows))
            info=snapshot['metadata'][symbol]
            # Reglas fijadas antes de consultar el holdout; no se optimiza una rejilla.
            result=dict(symbol=symbol,rows=len(rows),development=replay(rows,info,start=start,end=holdout),
                        holdout=replay(rows,info,start=holdout,end=end),stress=replay(rows,info,start=holdout,end=end,cost_multiple=2),
                        legacySame15m=replay(rows,info,start=holdout,end=end,legacy=True))
            print(json.dumps(result,ensure_ascii=False),flush=True)
            return result
    try:
        results=await asyncio.gather(*(evaluate(c) for c in candidates[:16]),return_exceptions=True)
        report=dict(at=time.time(),policy=desk.POLICY_VERSION,interval='15m',holdoutFrom=holdout,end=end,
            limitations=['Universo actual: sesgo de supervivencia','Cuentas por símbolo, no cartera conjunta',
                          'Spread supuesto de 2 pb; sin histórico de profundidad','Comparador legacy en 15m, no reproducción exacta del antiguo 5m',
                          'Diagnóstico, sin autorización de promoción ni garantía económica'],
            results=[r if isinstance(r,dict) else dict(error=str(r)) for r in results])
        (ROOT/'reports'/'desk-crypto-review-2026-10-18.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    finally: await market_data.close_client()


if __name__=='__main__': asyncio.run(main())
