import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import experiments, research_plans


class FamilyWalkForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'registry.sqlite'

    def dataset(self,symbol='BTC',interval='1d'):
        seconds=research_plans.SECONDS[interval]
        return dict(source='fixture',symbol=symbol,interval=interval,candles=[
            dict(time=seconds*(i+1),open=100+i*.1,close=100.05+i*.1,
                 high=101+i*.1,low=99+i*.1,volume=100) for i in range(600)])

    def test_three_expanding_folds_purge_label_lifespan_and_embargo_holdout(self):
        p=experiments.purged_walk_forward(600,60,48,2)
        self.assertEqual(len(p['folds']),3)
        for i,f in enumerate(p['folds']):
            self.assertEqual(f['test'][0]-f['train'][1],50)
            self.assertEqual(f['embargo'][1]-f['embargo'][0],2)
            self.assertLess(f['test'][0],f['test'][1])
            if i: self.assertGreaterEqual(f['test'][0]-p['folds'][i-1]['test'][1],2)
        self.assertGreaterEqual(p['holdout'][0]-p['folds'][-1]['test'][1],50)
        self.assertFalse(experiments.purged_walk_forward(80,60,48)['sufficient'])

    def test_a1_a2_do_not_block_a3_a4_but_same_family_reuse_is_preserved(self):
        for base,overlay,interval in [('A1','A3','1d'),('A2','A4','4h')]:
            first=research_plans.preregister_plan(base,symbols=['BTC'],db_path=self.path)
            research_plans.evaluate_plan(first['id'],[self.dataset(interval=interval)],db_path=self.path)
            symbols=['BTC','ETH'] if overlay=='A3' else ['BTC']
            second=research_plans.preregister_plan(overlay,symbols=symbols,
                benchmark='BTC' if overlay=='A3' else None,db_path=self.path)
            datasets=[self.dataset(s,interval) for s in symbols]
            result=research_plans.evaluate_plan(second['id'],datasets,db_path=self.path)
            btc=next(r for r in result['results'] if r['symbol']=='BTC')
            self.assertNotEqual(btc['status'],'previously_seen')
            self.assertTrue(btc['claim']['crossFamilyOverlap'])
            self.assertFalse(btc['claim']['independentFuture'])
            self.assertEqual(len(btc['folds']),3)
            repeat=research_plans.evaluate_plan(second['id'],datasets,db_path=self.path)
            self.assertTrue(all(r['status']=='previously_seen' for r in repeat['results']))
        self.assertEqual(experiments.accumulated_trials(db_path=self.path),4)
        self.assertTrue(experiments.verify_ledger(self.path))

    def test_future_cross_family_overlap_is_descriptive_and_cannot_be_promoted(self):
        with patch.object(experiments.time,'time',return_value=1000):
            a=research_plans.preregister_plan('A2',symbols=['BTC'],db_path=self.path)
            b=research_plans.preregister_plan('A4',symbols=['BTC'],db_path=self.path)
        dataset=dict(source='fixture',symbol='BTC',interval='4h',start=1100,end=1200,dataHash='fixed')
        with patch.object(experiments.time,'time',return_value=1300):
            experiments.claim_holdout(a['id'],dataset,self.path)
            claim=experiments.claim_holdout(b['id'],dataset,self.path)
            result=experiments.finalize_evidence(b['id'],{},[dict(passed=True)],claim,db_path=self.path)
        self.assertTrue(claim['allowed'])
        self.assertFalse(result['independentFuture'])
        self.assertFalse(result['promotable'])

    def test_existing_a1_cohort_does_not_block_new_relative_strength_family(self):
        legacy=experiments.register_experiment('A1',dict(plan=dict(id='A1'),parameters=dict(lookback=55),
            costModel=dict(version='net-costs-v1'),trialCount=1),db_path=self.path)
        dataset=dict(source='fixture',symbol='BTC',interval='1d',start=1100,end=1200,dataHash='legacy')
        experiments.claim_holdout(legacy['id'],dataset,self.path)
        current=research_plans.preregister_plan('A3',symbols=['BTC','ETH'],benchmark='BTC',db_path=self.path)
        claim=experiments.claim_holdout(current['id'],dataset,self.path)
        self.assertTrue(claim['allowed'])
        self.assertTrue(claim['crossFamilyOverlap'])
        self.assertFalse(claim['independentFuture'])

    def test_holdout_price_mutation_cannot_change_validation(self):
        dataset=self.dataset()
        exp=research_plans.preregister_plan('A1',symbols=['BTC'],db_path=self.path)
        first=research_plans.evaluate_plan(exp['id'],[dataset],db_path=self.path)['results'][0]
        changed=deepcopy(dataset)
        for row in changed['candles'][480:]:
            for key in ('open','high','low','close'): row[key]*=2
        # A separate registry permits a second diagnostic run without bypassing
        # the immutable reuse guard of the original family.
        other=self.path.with_name('other.sqlite')
        second_exp=research_plans.preregister_plan('A1',symbols=['BTC'],db_path=other)
        second=research_plans.evaluate_plan(second_exp['id'],[changed],db_path=other)['results'][0]
        self.assertEqual(first['folds'],second['folds'])
        self.assertEqual(first['validationUncertainty'],second['validationUncertainty'])
