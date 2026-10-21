"""Órdenes post-only experimentales sobre operaciones observadas, sin pasarela.

No supone un fill por tocar el bid/ask. Descuenta la cola publicada y exige
operaciones agresoras posteriores; sin datos, cancela. Es un modelo de cola
conservador, no una reconstrucción de la prioridad real del exchange.
"""

import argparse
from dataclasses import asdict, dataclass, field
import json
import math
from decimal import Decimal, ROUND_DOWN
from pathlib import Path


@dataclass
class PostOnlyOrder:
    symbol: str
    side: str
    price: float
    quantity: float
    placed_at: float
    expires_at: float
    queue_ahead: float
    fee_rate: float
    quantity_step: float = 0.0
    status: str = 'pending'
    filled: float = 0.0
    fees: float = 0.0
    last_id: int = -1
    last_at: float = 0.0
    fills: list = field(default_factory=list)

    @classmethod
    def place(cls,symbol,side,quantity,book,*,now,ttl,fee_rate):
        bid,ask=float(book['bidPrice']),float(book['askPrice'])
        queue=float(book['bidQty' if side=='buy' else 'askQty'])
        if side not in ('buy','sell') or not symbol:
            raise ValueError('Lado o mercado inválido')
        values=(bid,ask,quantity,now,ttl)
        if not all(math.isfinite(x) and x>0 for x in values) or ask<=bid:
            raise ValueError('Libro cruzado o precio/cantidad inválidos')
        if not math.isfinite(queue) or queue<0 or not math.isfinite(fee_rate) or not 0<=fee_rate<1:
            raise ValueError('Cola o comisión inválidas')
        stamp=float(book['at'])
        if not math.isfinite(stamp) or not 0<=now-stamp<=2:
            raise ValueError('Libro antiguo')
        return cls(symbol,side,bid if side=='buy' else ask,quantity,now,now+ttl,queue,fee_rate,last_at=now)

    def advance(self,now):
        if now>=self.expires_at and self.status=='pending':
            self.status='expired'

    def disconnect(self):
        if self.status=='pending': self.status='cancelled-data-gap'

    def trade(self,event):
        if self.status!='pending' or event.get('symbol')!=self.symbol: return
        stamp,price,qty=event['at'],event['price'],event['quantity']
        if not all(isinstance(x,(int,float)) and math.isfinite(x) and x>0 for x in (stamp,price,qty)):
            return
        identifier=event.get('id')
        if type(identifier) is not int or identifier<=self.last_id or stamp<self.last_at or stamp<=self.placed_at:
            return
        self.advance(stamp)
        if self.status!='pending': return
        self.last_id,self.last_at=identifier,stamp
        # Binance isBuyerMaker=true implica venta agresora al bid.
        buyer_maker=event.get('isBuyerMaker')
        if type(buyer_maker) is not bool or buyer_maker!=(self.side=='buy'):
            return
        if (self.side=='buy' and price>self.price) or (self.side=='sell' and price<self.price):
            return
        if price==self.price:
            ahead=min(qty,self.queue_ahead)
            self.queue_ahead-=ahead
            qty-=ahead
        else:
            self.queue_ahead=0.0
        fill=min(qty,float(Decimal(str(self.quantity))-Decimal(str(self.filled))))
        if self.quantity_step>0:
            step=Decimal(str(self.quantity_step))
            fill=float((Decimal(str(fill))/step).to_integral_value(rounding=ROUND_DOWN)*step)
        if fill<=0: return
        fee=fill*self.price*self.fee_rate
        self.filled=float(Decimal(str(self.filled))+Decimal(str(fill)))
        self.fees+=fee
        self.fills.append(dict(at=stamp,tradeId=identifier,quantity=fill,price=self.price,fee=fee))
        if self.quantity-self.filled<=self.quantity*1e-12:
            self.status='filled'

    def public(self):
        cash=self.filled*self.price
        return dict(**asdict(self),netCashFlow=(-cash-self.fees if self.side=='buy' else cash-self.fees),
            mode='paper-post-only',queueModel='displayed-queue-no-cancellations',commissionVerified=False,
            executionVerified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True,help='JSONL: primer libro y operaciones posteriores')
    parser.add_argument('--symbol',required=True)
    parser.add_argument('--side',choices=('buy','sell'),required=True)
    parser.add_argument('--quantity',type=float,required=True)
    parser.add_argument('--fee-rate',type=float,required=True,help='Supuesto explícito por lado, sin descuento automático')
    parser.add_argument('--ttl',type=float,default=60)
    args=parser.parse_args()
    with args.input.open(encoding='utf-8') as stream:
        book=json.loads(next(stream))
        if book.get('symbol')!=args.symbol: raise ValueError('El libro pertenece a otro mercado')
        order=PostOnlyOrder.place(args.symbol,args.side,args.quantity,book,now=book['at'],ttl=args.ttl,fee_rate=args.fee_rate)
        for line in stream:
            event=json.loads(line)
            if event.get('type')=='disconnect': order.disconnect()
            else: order.trade(event)
        # Un registro acabado no prueba que la orden pendiente se ejecutase.
        order.disconnect()
        print(json.dumps(order.public(),ensure_ascii=False,allow_nan=False,indent=2))


if __name__=='__main__':
    main()
