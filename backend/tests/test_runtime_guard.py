import unittest
from unittest.mock import Mock,patch
from app import runtime_guard as guard


class RuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        self.patches=[patch.object(guard,'_state',dict(supported=True,active=False,error=None)),
                      patch.object(guard,'_api',None),patch.object(guard,'_previous',None),patch.object(guard,'_owner',None),
                      patch.object(guard.sys,'platform','win32'),patch.dict(guard.os.environ,{'TRADING_KEEP_AWAKE':'1'})]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def test_scoped_request_keeps_display_policy_and_restores_previous_state(self):
        api=Mock(return_value=guard.CONTINUOUS)
        with patch.object(guard,'_native_api',return_value=api):
            self.assertTrue(guard.acquire()['active'])
            guard.acquire()
            api.assert_called_once_with(guard.CONTINUOUS|guard.SYSTEM_REQUIRED)
            guard.release()
        self.assertEqual(api.call_args.args[0],guard.CONTINUOUS)
        self.assertFalse(guard.status()['active'])
        self.assertFalse(guard.status()['displayRequired'])

    def test_rejection_is_visible_and_does_not_claim_protection(self):
        with patch.object(guard,'_native_api',return_value=Mock(return_value=0)):
            result=guard.acquire()
        self.assertFalse(result['active'])
        self.assertEqual(result['error'],'OSError')

    def test_explicit_opt_out_makes_no_windows_call(self):
        with patch.dict(guard.os.environ,{'TRADING_KEEP_AWAKE':'0'}),patch.object(guard,'_native_api') as api:
            guard.acquire()
        api.assert_not_called()
