import subprocess
import os
import tempfile
from pathlib import Path
import unittest
from gnvitop import server, preferences

class MetricsTests(unittest.TestCase):
    def test_power_and_legacy_or_unsupported_devices(self):
        for suffix, expected in [(', 84.25, 450', (84.25,450.0)), (', [N/A], Not Supported',(None,None)), ('',(None,None)), (', nan, inf',(None,None)), (', 0, 0',(0.0,0.0))]:
            with self.subTest(suffix=suffix):
                gpu=server._build_gpus('0, GPU, 10000, 2000, 8000, 20, 45'+suffix)[0]
                self.assertEqual((gpu['power_draw_w'],gpu['power_limit_w']),expected)
                self.assertEqual(gpu['memory_usage_pct'],20)

    def test_pmon_headers_across_driver_versions(self):
        for extra in [False, True]:
            header='# gpu pid type sm mem enc dec '+('jpg ofa ' if extra else '')+'fb '+('ccpm ' if extra else '')+'command'
            row='0 123 C 37 8 0 0 '+('0 0 ' if extra else '')+'2048 '+('0 ' if extra else '')+'python'
            script="nvidia-smi() { cat <<'DATA'\n"+header+'\n# units\n'+row+"\n0 - - - - - - -\nDATA\n}\n"+server._PROC_QUERY
            with tempfile.TemporaryDirectory() as directory:
                ps = Path(directory)/'ps'
                ps.write_text('#!/bin/sh\nprintf "123 researcher_long_username python\\n"\n')
                ps.chmod(0o755)
                output=subprocess.run(['bash','-c',script],capture_output=True,text=True,check=True,env={**os.environ,'PATH':directory+os.pathsep+os.environ['PATH']})
            self.assertEqual(output.stderr,'')
            gpus=[{'index':0,'processes':[]}]
            server._attach_processes(gpus,output.stdout)
            process=gpus[0]['processes'][0]
            self.assertEqual(process['gpu_memory_mb'],2048)
            self.assertEqual(process['sm_utilization_pct'],37)
            self.assertEqual(process['user'],'researcher_long_username')

    def test_missing_metrics_and_malformed_rows(self):
        gpus=[{'index':0,'processes':[]}]
        server._attach_processes(gpus,'12,0,-,alice,python,-\n13,0,500,bob,python,0\n14,0,42,alice,old\n15,not-a-gpu,42,bob,python,5')
        procs=gpus[0]['processes']
        self.assertEqual(len(procs),3)
        self.assertIsNone(procs[0]['gpu_memory_mb'])
        self.assertIsNone(procs[0]['sm_utilization_pct'])
        self.assertEqual(procs[1]['sm_utilization_pct'],0)
        self.assertIsNone(procs[2]['sm_utilization_pct'])

    def test_username_overrides_validate_and_deduplicate(self):
        data={'hosts':{'lab':{'my_users':['alice',' alice ','container-user']}}}
        self.assertEqual(preferences.validate(data)['hosts']['lab']['my_users'],['alice','container-user'])
        for users in ['alice',['bad\nname'],[None],['u']*21]:
            with self.subTest(users=users),self.assertRaises(ValueError):
                preferences.validate({'hosts':{'lab':{'my_users':users}}})
