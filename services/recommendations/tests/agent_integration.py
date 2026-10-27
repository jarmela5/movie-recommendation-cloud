"""Runs against the isolated integration stack; never normal user accounts."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uuid
import time
import httpx
from agent import run_cycle


def main():
    token = os.environ['INTERNAL_API_TOKEN']
    user_url = os.environ.get('USER_SERVICE_URL', 'http://user:8000')
    rec_url = 'http://localhost:8000'  # Script runs inside Recommendations.
    name = 'agent-' + uuid.uuid4().hex[:10]
    with httpx.Client(base_url='http://frontend', timeout=15, trust_env=False) as browser, \
         httpx.Client(trust_env=False) as internal:
        for attempt in range(30):
            try:
                if (browser.get('/api/recommendations/latest').status_code == 401
                        and browser.get('/api/user/me').status_code == 401):
                    break
            except httpx.RequestError:
                pass
            time.sleep(1)
        else:
            raise AssertionError('Test services did not become ready')
        assert internal.get(user_url + '/internal/profiles').status_code == 401
        assert internal.get(user_url + '/internal/profiles', headers={'X-Internal-Token': 'wrong'}).status_code == 401
        assert browser.get('/internal/profiles').status_code == 404
        assert browser.post('/internal/recommendations/1/refresh').status_code == 404
        assert browser.post('/api/user/register', json={'username': name, 'email': name+'@example.test',
                                                       'password': 'agent-test-password'}).status_code == 201
        profile = browser.put('/api/user/profile', json={'is_public': 0, 'likes': ['Comedy'], 'avoids': ['Horror']}).json()
        uid = profile['user_id']
        assert browser.get('/api/recommendations/latest').json()['status'] == 'missing'
        headers = {'X-Internal-Token': token}
        listing = internal.get(user_url + '/internal/profiles', headers=headers).json()
        assert {'user_id': uid, 'revision': profile['revision']} in listing
        assert all(set(row) == {'user_id', 'revision'} for row in listing)
        assert internal.get(user_url + '/internal/profiles', headers=headers, params={'limit': 0}).status_code == 400
        # No user is logged in while the worker proactively prepares results.
        browser.post('/api/user/logout')
        counts = run_cycle(internal, user_url, rec_url, token)
        assert counts['generated'] >= 1 and counts['failed'] == 0, counts
        assert browser.get('/api/recommendations/latest').status_code == 401
        assert browser.post('/api/user/login', json={'identifier': name, 'password': 'agent-test-password'}).status_code == 200
        saved = browser.get('/api/recommendations/latest').json()
        assert saved['status'] == 'fresh'
        assert saved['result']['method'] == 'local_llm'
        assert saved['result']['recommendations']
        assert all('Comedy' in m['genres'] and 'Horror' not in m['genres'] for m in saved['result']['recommendations'])
        counts = run_cycle(internal, user_url, rec_url, token)
        assert counts['generated'] == 0 and counts['failed'] == 0
        assert browser.get('/api/recommendations/latest').json() == saved
        browser.put('/api/user/profile', json={'likes': ['Adventure'], 'avoids': ['Horror']})
        assert browser.get('/api/recommendations/latest').json()['status'] == 'stale'
        counts = run_cycle(internal, user_url, rec_url, token)
        assert counts['generated'] == 1 and counts['failed'] == 0
        latest = browser.get('/api/recommendations/latest').json()
        assert latest['status'] == 'fresh' and latest['profile_revision'] > saved['profile_revision']
        assert all('Adventure' in m['genres'] for m in latest['result']['recommendations'])
    print('Agent integration passed: private profiles, internal auth, logged-out generation, cache hit, profile refresh.')


if __name__ == '__main__':
    main()
