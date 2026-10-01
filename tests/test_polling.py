import threading
import time
import unittest
from unittest.mock import patch, Mock
from gnvitop import server


class PollingTests(unittest.TestCase):
    def setUp(self):
        server._refresh_done.wait(2)
        server.cache.update(data=[], last_update=0, revision=0)
        server._bg_refresh_running=False
        server._refresh_done=threading.Event()
        server._refresh_done.set()
        server._host_failures.clear()
        self.host={'alias':'test','hostname':'test.local','user':'alice','port':22}
        self.result={'alias':'test','status':'ok','gpus':[]}

    def test_concurrent_refreshes_share_one_poll_and_cache_reads_do_not_block(self):
        entered, release = threading.Event(), threading.Event()
        def fetch(on_result=None):
            on_result(self.result)
            entered.set()
            release.wait(2)
            return [self.result]
        with patch.object(server,'fetch_all_gpu_info',side_effect=fetch) as poll:
            done=server._trigger_background_refresh()
            self.assertTrue(entered.wait(1))
            try:
                events=[]
                threads=[threading.Thread(target=lambda:events.append(server._trigger_background_refresh(force=True))) for _ in range(8)]
                for t in threads:t.start()
                for t in threads:t.join(1)
                self.assertEqual(len(events),8)
                self.assertTrue(all(e is done for e in events))
                snapshot=server.app.test_client().get('/api/gpus').json
                self.assertEqual(snapshot['hosts'],[self.result])
                self.assertFalse(done.is_set())
                self.assertEqual(poll.call_count,1)
            finally:
                release.set()
                self.assertTrue(done.wait(2))
            # Repeated manual clicks within five seconds use the completed snapshot.
            self.assertIs(server._trigger_background_refresh(force=True),done)
            self.assertEqual(poll.call_count,1)

    def test_warm_stream_and_history_reuse_cache(self):
        server.cache.update(data=[self.result],last_update=time.time(),revision=1)
        with patch.object(server,'fetch_all_gpu_info') as poll:
            response=server.app.test_client().get('/api/stream').get_data(as_text=True)
            self.assertIn('"alias": "test"',response)
            self.assertIn('"done": true',response)
            self.assertEqual(server.cached_gpu_info(),[self.result])
            poll.assert_not_called()

    def test_failure_backoff_then_recovery(self):
        error={'alias':'test','status':'error','gpus':[]}
        with patch.object(server,'query_gpu',side_effect=[error,self.result]) as query, patch.object(server.time,'monotonic',return_value=100) as clock:
            self.assertEqual(server._query_with_backoff(self.host,{}),error)
            clock.return_value=110
            self.assertEqual(server._query_with_backoff(self.host,{}),error)
            self.assertEqual(query.call_count,1)
            clock.return_value=131
            self.assertEqual(server._query_with_backoff(self.host,{}),self.result)
            self.assertFalse(server._host_failures)

    def test_refresh_failure_releases_waiters(self):
        with patch.object(server,'fetch_all_gpu_info',side_effect=RuntimeError('test')), patch.object(server.app.logger,'exception'):
            done=server._trigger_background_refresh()
            self.assertTrue(done.wait(2))
            self.assertFalse(server._bg_refresh_running)

    def test_failed_ssh_connect_is_closed(self):
        client=Mock()
        client.connect.side_effect=OSError('unreachable')
        with patch.object(server.paramiko,'SSHClient',return_value=client):
            with self.assertRaises(OSError):
                server._make_ssh_client('test',22,'alice',None)
        client.close.assert_called_once()

    def test_remote_command_failure_closes_connection(self):
        client=Mock()
        client.exec_command.side_effect=TimeoutError()
        with patch.object(server,'_make_ssh_client',return_value=client):
            result=server.query_gpu(self.host)
        self.assertEqual(result['status'],'error')
        client.close.assert_called_once()
