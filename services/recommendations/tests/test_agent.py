import unittest
import httpx
from agent import run_cycle


class AgentTests(unittest.TestCase):
    def test_pagination_failure_isolation_and_retry(self):
        calls = []
        def transport(request):
            calls.append(request)
            self.assertEqual(request.headers['X-Internal-Token'], 'test-token')
            self.assertNotIn('cookie', request.headers)
            if request.method == 'GET':
                after = int(request.url.params['after'])
                return httpx.Response(200, json=[{'user_id': i} for i in range(1, 51)] if after == 0
                    else [{'user_id': 51}] if after == 50 else [])
            uid = int(request.url.path.split('/')[-2])
            return httpx.Response(503) if uid == 2 else httpx.Response(200, json={'status': 'generated' if uid == 1 else 'skipped'})
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            for _ in range(2):
                self.assertEqual(run_cycle(client, 'http://user', 'http://rec', 'test-token'),
                                 {'generated': 1, 'skipped': 49, 'failed': 1})
        self.assertEqual(sum(r.method == 'POST' for r in calls), 102)

    def test_nonadvancing_page_and_unavailable_user_stop_cycle(self):
        for handler in (lambda r: httpx.Response(200, json=[{'user_id': 0}]),
                        lambda r: httpx.Response(503)):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises((ValueError, httpx.HTTPError)):
                    run_cycle(client, 'http://user', 'http://rec', 'test-token')


if __name__ == '__main__':
    unittest.main()
