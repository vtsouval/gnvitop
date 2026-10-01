import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from gnvitop import server

GPU = 'NVIDIA\n0, GPU, 1000, 200, 800, 20, 40, 50, 200\n---SEP---\n'
RESOURCE = '''---HOST-RESOURCES---
MemTotal: 10000
MemAvailable: 7500
---HOME-DISK---
Filesystem 1024-blocks Used Available Capacity Mounted on
/dev/test 1000 500 450 53% /home with spaces
---ALL-DISKS---
/dev/test 1000 500 450 53% /home with spaces
/dev/other 2000 1000 900 53% /data
server:/shared 4000 1000 3000 25% /shared
'''

class ResourceTests(unittest.TestCase):
    def test_memory_home_network_disks_and_deduplication(self):
        gpu, result = server._parse_host_resources(GPU + RESOURCE)
        self.assertEqual(gpu, GPU.strip())
        self.assertEqual(result['memory']['used_bytes'], 2500 * 1024)
        self.assertEqual(result['home_disk']['mount'], '/home with spaces')
        self.assertEqual(result['home_disk']['usage_pct'], 53)
        self.assertEqual(len(result['disks']), 3)
        self.assertEqual(result['disks'][2]['device'], 'server:/shared')

    def test_unsupported_missing_and_malformed(self):
        for output in (GPU, GPU + '---HOST-RESOURCES---\nMemTotal: 12\nMemAvailable: 13\n---HOME-DISK---\n/dev/x 0 0 0 0% /\n---ALL-DISKS---\nbad'):
            gpu, result = server._parse_host_resources(output)
            self.assertIsNone(result['memory'])
            self.assertIsNone(result['home_disk'])
            self.assertEqual(result['disks'], [])
            self.assertTrue(server._build_gpus(server._parse_combined_output(gpu)[0]))

    def test_remote_collection_uses_one_ssh_command(self):
        client = Mock()
        client.exec_command.return_value = (None, io.BytesIO((GPU + RESOURCE).encode()), None)
        host = dict(alias='test', hostname='test', user='researcher', port=22)
        with patch.object(server, '_make_ssh_client', return_value=client):
            result = server.query_gpu(host)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['gpus']), 1)
        self.assertEqual(result['resources']['memory']['used_bytes'], 2500 * 1024)
        client.exec_command.assert_called_once()
        self.assertIn('---HOST-RESOURCES---', client.exec_command.call_args.args[0])

    def test_shell_failed_disk_keeps_gpu_output(self):
        with tempfile.TemporaryDirectory() as directory:
            timeout = Path(directory) / 'timeout'
            timeout.write_text('#!/bin/sh\nexit 124\n')
            timeout.chmod(0o755)
            output = subprocess.run(['bash', '-c', "printf '%s\\n' NVIDIA; " + server._RESOURCE_QUERY], capture_output=True, text=True, check=True, env={**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH']})
        self.assertTrue(output.stdout.startswith('NVIDIA\n'))
        _, resource = server._parse_host_resources(output.stdout)
        self.assertIsNone(resource['home_disk'])
        self.assertEqual(resource['disks'], [])

    def test_reserved_space_and_full_disk(self):
        _, resource = server._parse_host_resources('---HOST-RESOURCES---\n---HOME-DISK---\n/dev/x 1000 990 -2 101% /\n')
        self.assertEqual(resource['home_disk']['available_bytes'], 0)
        self.assertEqual(resource['home_disk']['usage_pct'], 100)
