
import json
import sys
import uuid
from pathlib import Path
import httpx

CHECKPOINT = Path('/app/data/persistence-test.json')


def expect(response, status=200):
    assert response.status_code == status, (response.status_code, response.text)
    return response.json()


def main(phase):
    with httpx.Client(base_url='http://frontend', timeout=15, trust_env=False) as client:
        if phase == 'prepare':
            name = 'persist-' + uuid.uuid4().hex[:8]
            password = 'temporary-integration-password'
            expect(client.post('/api/user/register', json={
                'username': name, 'email': name + '@example.test', 'password': password}), 201)
            assert expect(client.get('/api/recommendations/latest'))['status'] == 'missing'
            expect(client.put('/api/user/profile', json={'likes': ['Comedy'], 'avoids': ['Horror']}))
            result = expect(client.post('/api/recommendations/generate', json={'limit': 3}))
            saved = expect(client.get('/api/recommendations/latest'))
            assert saved['result'] == result and saved['status'] == 'fresh'
            CHECKPOINT.write_text(json.dumps(dict(name=name, password=password, saved=saved)))
            print('Prepared: authenticated selection stored in test volume.')
        elif phase == 'verify':
            checkpoint = json.loads(CHECKPOINT.read_text())
            expect(client.post('/api/user/login', json={
                'identifier': checkpoint['name'], 'password': checkpoint['password']}))
            assert expect(client.get('/api/recommendations/latest')) == checkpoint['saved']
            expect(client.put('/api/user/profile', json={'likes': ['Adventure'], 'avoids': ['Horror']}))
            stale = expect(client.get('/api/recommendations/latest'))
            assert stale['status'] == 'stale'
            assert stale['result'] == checkpoint['saved']['result']
            expect(client.post('/api/recommendations/generate', json={'limit': 3}))
            assert expect(client.get('/api/recommendations/latest'))['status'] == 'fresh'
            expect(client.post('/api/user/logout'))
            expect(client.get('/api/recommendations/latest'), 401)
            name = 'isolated-' + uuid.uuid4().hex[:8]
            expect(client.post('/api/user/register', json={
                'username': name, 'email': name + '@example.test', 'password': 'temporary-password'}), 201)
            assert expect(client.get('/api/recommendations/latest'))['status'] == 'missing'
            CHECKPOINT.unlink()
            print('Persistence integration passed: recreation, staleness, refresh, logout and isolation.')
        else:
            raise ValueError('Expected prepare or verify')


if __name__ == '__main__':
    main(sys.argv[1])
