import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


spec=importlib.util.spec_from_file_location('retry_oracle',Path(__file__).resolve().parents[2]/'deploy'/'retry_oracle.py')
retry=importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)


def plan():
    return dict(region='eu-madrid-1',displayName='test',compartmentId='ocid1.compartment.test',sshPublicKey='ssh-ed25519 public',requests=[
        dict(shape='VM.Standard.A1.Flex',architecture='aarch64',shapeConfig=dict(ocpus=1,memoryInGBs=6),availabilityDomain='AD',imageId='ocid1.image.arm',subnetId='ocid1.subnet.test'),
        dict(shape='VM.Standard.E2.1.Micro',architecture='x86_64',availabilityDomain='AD',imageId='ocid1.image.amd',subnetId='ocid1.subnet.test')])


class OracleRetryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.journal=Path(self.folder.name)/'retry.sqlite'
        self.client=Mock()
        self.client.list_instances.return_value=NS(data=[])
        self.client.launch_instance.return_value=NS(data=NS(id='ocid1.instance.created'))
        models=NS(**{name:(lambda **kwargs:NS(**kwargs)) for name in
            ('CreateVnicDetails','InstanceSourceViaImageDetails','LaunchInstanceShapeConfigDetails','LaunchInstanceDetails')})
        self.oci=NS(config=NS(from_file=Mock(return_value={})),
            retry=NS(NoneRetryStrategy=Mock()),core=NS(ComputeClient=Mock(return_value=self.client),models=models),
            pagination=NS(list_call_get_all_results=lambda method,**kwargs:method(**kwargs)))

    def test_success_stops_future_launches_and_preserves_one_token(self):
        self.assertEqual(retry.attempt(plan(),self.journal,self.oci,now=1000)['status'],'created')
        self.assertEqual(retry.attempt(plan(),self.journal,self.oci,now=3000)['status'],'created')
        self.client.launch_instance.assert_called_once()
        self.assertTrue(self.client.launch_instance.call_args.kwargs['opc_retry_token'])

    def test_only_confirmed_capacity_failure_alternates_shape_after_backoff(self):
        capacity=RuntimeError()
        capacity.status=500;capacity.code='InternalError';capacity.message='Out of host capacity'
        self.client.launch_instance.side_effect=[capacity,NS(data=NS(id='created'))]
        self.assertEqual(retry.attempt(plan(),self.journal,self.oci,now=1000)['status'],'capacity')
        self.assertEqual(retry.attempt(plan(),self.journal,self.oci,now=1200)['status'],'backoff')
        self.assertEqual(retry.attempt(plan(),self.journal,self.oci,now=2000)['status'],'created')
        self.assertEqual(self.client.launch_instance.call_args.args[0].shape,'VM.Standard.E2.1.Micro')

    def test_timeout_is_uncertain_and_never_creates_another_instance(self):
        self.client.launch_instance.side_effect=TimeoutError()
        self.assertEqual(retry.attempt(plan(),self.journal,self.oci,now=1000)['status'],'uncertain')
        self.assertEqual(retry.attempt(plan(),self.journal,self.oci,now=5000)['status'],'uncertain')
        self.client.launch_instance.assert_called_once()

    def test_existing_instance_prevents_launch_and_wrong_architecture_is_rejected(self):
        self.client.list_instances.return_value=NS(data=[NS(lifecycle_state='RUNNING')])
        self.assertEqual(retry.attempt(plan(),self.journal,self.oci,now=1000)['status'],'exists')
        self.client.launch_instance.assert_not_called()
        invalid=plan();invalid['requests'][1]['architecture']='aarch64'
        with self.assertRaises(ValueError):retry.validate(invalid)
