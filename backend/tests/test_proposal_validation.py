import unittest

from app.research import validation_checks


class ProposalValidationTests(unittest.TestCase):
    def test_positive_eight_trade_example_still_fails_uncertainty_and_neighbors(self):
        checks=validation_checks(dict(trades=8,pnl=3),dict(pnl=2.12),dict(lower95=-.38),dict(positiveBlocks=3),
                                 [dict(pnl=-4.38,stressPnl=-6.24),dict(pnl=-1.98,stressPnl=-2.59)])
        self.assertEqual({c['id'] for c in checks if not c['passed']},{'sample','uncertainty','neighbors'})

    def test_every_original_control_is_required_even_with_large_sample(self):
        for lower,blocks,neighbors in ((-.1,3,[dict(pnl=1,stressPnl=1)]),(.1,2,[dict(pnl=1,stressPnl=1)]),(.1,3,[dict(pnl=1,stressPnl=-1)])):
            checks=validation_checks(dict(trades=100,pnl=10),dict(pnl=8),dict(lower95=lower),dict(positiveBlocks=blocks),neighbors)
            self.assertFalse(all(c['passed'] for c in checks))
