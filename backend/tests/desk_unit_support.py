"""Explicit fixtures for desk unit tests; portfolio integration is tested apart."""
import time
from unittest.mock import patch

from app import desk


def fund_unit_desk(case, engine):
    engine.state['funding']=dict(currency='EUR',amount=engine.initial,
        netQuotePerEuro=1.0,at=time.time(),source='UNIT TEST fixture',virtual=True)
    # These tests isolate paper mechanics, not aggregate portfolio allocation.
    case.enterContext(patch.object(desk.portfolio_risk,'assess',return_value=dict(
        allowed=True,reason='Isolated unit-test portfolio',token=None)))
