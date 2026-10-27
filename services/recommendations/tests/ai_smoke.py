
import time
import uuid
import httpx


def main():
    name = 'ai-' + uuid.uuid4().hex[:10]
    with httpx.Client(base_url='http://frontend', timeout=210, trust_env=False) as client:
        for _ in range(30):
            try:
                if client.get('/api/user/me').status_code == 401 and client.get('/api/movies').status_code == 200 and client.post('/api/recommendations/generate').status_code == 401:
                    break
            except httpx.RequestError:
                pass
            time.sleep(1)
        else:
            raise AssertionError('Test services not ready; restart the test frontend after rebuilding services')
        response = client.post('/api/user/register', json={
            'username': name, 'email': name + '@example.test', 'password': 'temporary-test-password'})
        assert response.status_code == 201, response.text
        response = client.put('/api/user/profile', json={'likes': ['Comedy'], 'avoids': ['Horror']})
        assert response.status_code == 200, response.text
        for prompt in ('', 'Quero uma comédia dos anos 90', 'Quero filmes de terror', 'Quero comédia sem romance', 'Quero filmes com menos de 90 minutos'):
            start = time.monotonic()
            response = client.post('/api/recommendations/generate', json={'limit': 3, 'prompt': prompt})
            print(prompt or 'Profile only', response.status_code, round(time.monotonic() - start, 1), response.text, flush=True)
            if 'minutos' in prompt:
                assert response.status_code == 422, response.text
                continue
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['method'] == 'local_llm'
            latest = client.get('/api/recommendations/latest')
            assert latest.status_code == 200, latest.text
            assert latest.json()['status'] == 'fresh', latest.text
            assert latest.json()['result'] == data, latest.text
            assert latest.json()['prompt'] == prompt, latest.text
            movies = data['recommendations']
            if 'terror' in prompt:
                assert movies == [], data
                continue
            assert len(movies) == 3, data
            assert len({m['id'] for m in movies}) == 3
            for movie in movies:
                assert 'Comedy' in movie['genres'] and 'Horror' not in movie['genres'], movie
                if 'sem romance' in prompt:
                    assert 'Romance' not in movie['genres'], movie
                assert movie['reason'].strip()
                original = client.get('/api/movies/' + str(movie['id'])).json()
                assert original['title'] == movie['title']
            if 'anos 90' in prompt:
                assert data['applied_filters']['avoids'] == 'horror', data
                assert data['applied_filters']['year_from'] == 1990, data
                assert data['applied_filters']['year_to'] == 1999, data
                assert all(1990 <= int(m['title'][-5:-1]) <= 1999 for m in movies)
    print('Real-model smoke checks passed.', flush=True)


if __name__ == '__main__':
    main()
