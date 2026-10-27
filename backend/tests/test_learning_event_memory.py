import math
import time
from unittest.mock import patch

from app import learning
from test_learning_economics import EconomicDatabaseTests


class EventMemoryTests(EconomicDatabaseTests):
    def test_sparse_trading_keeps_fifty_net_closes_with_exact_exponential_expectancy(self):
        now=int(time.time())
        for age in range(60):
            self.trade(exit_time=now-age*2*86400,pnl=10 if age%2==0 else -10)
        stats=learning.analyze('scalper')
        self.assertEqual(stats['trades'],50)
        self.assertEqual(stats['oldestClose'],now-49*2*86400)
        bucket=stats['bySymbol']['BTC']
        weights=[learning.EVENT_DECAY**age for age in range(50)]
        expected=sum(w*(1 if age%2==0 else -1) for age,w in enumerate(weights))/sum(weights)
        self.assertEqual(bucket['expectancyR'],round(expected,3))
        self.assertEqual(bucket['pnl'],0)
        self.assertLess(bucket['effectiveN'],bucket['n'])

    def test_time_window_can_keep_more_than_fifty_and_does_not_include_old_extra_closes(self):
        now=int(time.time())
        for age in range(80): self.trade(exit_time=now-age*600,pnl=1)
        for age in range(10): self.trade(exit_time=now-(10+age)*86400,pnl=-10)
        self.assertEqual(learning.analyze('scalper')['trades'],80)

    def test_event_floor_never_crosses_symbol_revalidation_cutoff(self):
        now=int(time.time())
        for age in range(60): self.trade(exit_time=now-age*86400,pnl=-10)
        stats=learning.analyze('scalper',after_by_symbol={'BTC':now-3*86400})
        self.assertEqual(stats['trades'],3)
        self.assertEqual(stats['bySymbol']['BTC']['pnl'],-30)

    def test_future_nonfinite_and_incomplete_trades_do_not_fill_the_event_window(self):
        now=int(time.time())
        self.trade(exit_time=now+100,pnl=10)
        self.trade(exit_time=now-1,pnl=float('inf'))
        self.trade(exit_time=now-2,status='open')
        for age in range(50): self.trade(exit_time=now-3-age*86400,pnl=-1)
        stats=learning.analyze('scalper')
        self.assertEqual(stats['trades'],50)
        self.assertEqual(stats['referenceClose'],now-3)
        self.assertEqual(stats['bySymbol']['BTC']['pnl'],-50)

    def test_gross_legacy_and_unknown_cost_records_cannot_fill_the_event_floor(self):
        now=int(time.time())
        legacy=self.trade(exit_time=now,pnl=10000)
        unknown=self.trade(exit_time=now-1,pnl=10000)
        with learning._conn() as con:
            con.execute('UPDATE bot_trades SET snapshot=? WHERE id=?',('{"engineVersion":3}',legacy))
            con.execute('UPDATE bot_trades SET snapshot=NULL WHERE id=?',(unknown,))
        self.trade(exit_time=now-2,pnl=-1)
        stats=learning.analyze('scalper')
        self.assertEqual(stats['trades'],1)
        self.assertEqual(stats['bySymbol']['BTC']['pnl'],-1)

    def test_l1_soft_threshold_makes_exact_zeros_and_preserves_bias_gradient(self):
        model=learning._new_model()
        model['w']={'tiny':learning.META_LR*learning.META_L1/2,'large':1.0,'bias':.5}
        old_bias=.5*learning.META_FORGETTING
        predicted=1/(1+math.exp(-old_bias))
        learning._meta_sgd_step(model,{'bias':1.0,'tiny':0},True)
        self.assertEqual(model['w']['tiny'],0)
        self.assertAlmostEqual(model['w']['large'],learning.META_FORGETTING-learning.META_LR*learning.META_L1)
        self.assertAlmostEqual(model['w']['bias'],old_bias+learning.META_LR*(1-predicted))

    def test_future_closure_is_learned_only_after_its_observation_time_once(self):
        now=int(time.time())
        self.trade(exit_time=now+100,pnl=1)
        with patch.object(learning.time,'time',return_value=now):
            self.assertEqual(learning.meta_learn_new_trades('scalper'),0)
        with patch.object(learning.time,'time',return_value=now+100):
            self.assertEqual(learning.meta_learn_new_trades('scalper'),1)
            weights=dict(learning._load_model('scalper')['w'])
            self.assertEqual(learning.meta_learn_new_trades('scalper'),0)
            self.assertEqual(learning._load_model('scalper')['w'],weights)
