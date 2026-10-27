import unittest
import json
import sqlite3
from unittest.mock import patch
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from main import (
    app, get_client, get_store, _generation_slot,
    GENERATION_SLOTS, GENERATION_QUEUE_SIZE,
)
from storage import RecommendationStore
from inference import explicit_exclusion_genres


PROFILE = {"user_id": 12, "revision": 1, "likes_genres": "Comedy, Adventure", "avoids_genres": "Horror"}
MOVIE = {
    "id": 1, "title": "Toy Story (1995)", "genres": ["Comedy", "Adventure"],
    "rating": 3.92, "summary": "Dataset film", "release_date": "",
}


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = RecommendationStore(Path(self.directory.name) / "results.sqlite3")
        app.dependency_overrides[get_store] = lambda: self.store
        self.calls = []
        self.ai_reply = None
        self.intent = {'genres': [], 'avoids': [], 'year_from': None, 'year_to': None, 'unsupported': False}
        self.user_reply = httpx.Response(200, json=PROFILE)
        self.catalog_reply = httpx.Response(200, json=[MOVIE])

        def transport(request):
            self.calls.append(request)
            if request.url.path == '/v1/chat/completions':
                if self.ai_reply is not None:
                    if isinstance(self.ai_reply, Exception): raise self.ai_reply
                    return self.ai_reply
                data = json.loads(request.content)
                payload = json.loads(data['messages'][1]['content'])
                output = {'recommendations': [{'id': m['id']}
                          for m in payload['candidates']]} if 'candidates' in payload else self.intent
                return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]})
            reply = self.user_reply if request.url.path == "/api/user/me" or request.url.path.startswith("/internal/profiles/") else self.catalog_reply
            if isinstance(reply, Exception):
                raise reply
            return reply

        def client_override():
            with httpx.Client(transport=httpx.MockTransport(transport)) as client:
                yield client

        app.dependency_overrides[get_client] = client_override
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def generate(self, **kwargs):
        return self.client.post(
            "/api/recommendations/generate",
            headers={"Cookie": "session=test-session; unrelated=private"}, **kwargs,
        )

    def latest(self, **kwargs):
        return self.client.get('/api/recommendations/latest', headers={'Cookie': 'session=test-session'}, **kwargs)

    def refresh(self):
        return self.client.post('/internal/recommendations/12/refresh',
                                headers={'X-Internal-Token': 'test-internal'})

    def test_generation_capacity_matches_project_requirement(self):
        self.assertEqual(GENERATION_SLOTS, 4)
        self.assertEqual(GENERATION_QUEUE_SIZE, 0)

    def test_internal_refresh_auth_and_disabled_without_secret(self):
        with patch.dict('os.environ', {'INTERNAL_API_TOKEN': ''}):
            self.assertEqual(self.refresh().status_code, 401)
        with patch.dict('os.environ', {'INTERNAL_API_TOKEN': 'test-internal'}):
            self.assertEqual(self.client.post('/internal/recommendations/12/refresh').status_code, 401)
            self.assertEqual(self.client.post('/internal/recommendations/12/refresh',
                headers={'Cookie': 'session=test-session'}).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_agent_generates_skips_and_refreshes_without_session(self):
        with patch.dict('os.environ', {'INTERNAL_API_TOKEN': 'test-internal'}):
            self.assertEqual(self.refresh().json()['status'], 'generated')
            self.assertNotIn('cookie', self.calls[0].headers)
            self.assertEqual(self.calls[0].headers['X-Internal-Token'], 'test-internal')
            self.assertNotIn('X-Internal-Token', self.calls[1].headers)
            saved = self.store.latest(12)
            self.calls.clear()
            self.assertEqual(self.refresh().json()['status'], 'skipped')
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.store.latest(12), saved)
            self.user_reply = httpx.Response(200, json={**PROFILE, 'revision': 2})
            self.assertEqual(self.refresh().json()['status'], 'generated')
            self.assertEqual(self.store.latest(12)['profile_revision'], 2)

    def test_agent_failure_keeps_cache_and_empty_results_are_not_retried(self):
        with patch.dict('os.environ', {'INTERNAL_API_TOKEN': 'test-internal'}):
            self.assertEqual(self.refresh().status_code, 200)
            saved = self.store.latest(12)
            self.user_reply = httpx.Response(200, json={**PROFILE, 'revision': 2})
            self.ai_reply = httpx.ConnectError('private')
            self.assertEqual(self.refresh().status_code, 503)
            self.assertEqual(self.store.latest(12), saved)
            self.catalog_reply = httpx.Response(200, json=[])
            self.assertEqual(self.refresh().json()['status'], 'generated')
            self.assertEqual(self.store.latest(12)['result']['recommendations'], [])
            self.assertEqual(self.refresh().json()['status'], 'skipped')

    def test_agent_rejects_wrong_profile_and_respects_shared_slot(self):
        with patch.dict('os.environ', {'INTERNAL_API_TOKEN': 'test-internal'}):
            self.user_reply = httpx.Response(200, json={**PROFILE, 'user_id': 99})
            self.assertEqual(self.refresh().status_code, 502)
            for _ in range(GENERATION_SLOTS):
                _generation_slot.acquire()
            try:
                self.assertEqual(self.refresh().status_code, 503)
            finally:
                for _ in range(GENERATION_SLOTS):
                    _generation_slot.release()
            self.assertIsNone(self.store.latest(12))

    def test_latest_requires_valid_session(self):
        self.assertEqual(self.client.get('/api/recommendations/latest').status_code, 401)
        self.user_reply = httpx.Response(401)
        self.assertEqual(self.latest().status_code, 401)

    def test_latest_missing_and_saved_empty_are_distinct(self):
        self.assertEqual(self.latest().json()['status'], 'missing')
        self.catalog_reply = httpx.Response(200, json=[])
        self.assertEqual(self.generate().status_code, 200)
        saved = self.latest().json()
        self.assertEqual(saved['status'], 'fresh')
        self.assertEqual(saved['result']['recommendations'], [])
        self.assertEqual(saved['profile_revision'], 1)

    def test_latest_stores_request_and_result_without_inference(self):
        generated = self.generate(json={'limit': 3, 'prompt': 'Comedy'})
        self.assertEqual(generated.status_code, 200)
        self.calls.clear()
        response = self.latest()
        saved = response.json()
        self.assertEqual(saved['result'], generated.json())
        self.assertEqual(saved['prompt'], 'Comedy')
        self.assertEqual(saved['requested_limit'], 3)
        self.assertTrue(saved['generated_at'].endswith('+00:00'))
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(len(self.calls), 1)  # User only; neither Catalog nor AI.

    def test_latest_isolated_and_stale_after_profile_change(self):
        self.assertEqual(self.generate().status_code, 200)
        self.user_reply = httpx.Response(200, json={**PROFILE, 'user_id': 99})
        self.assertEqual(self.latest(params={'user_id': 12}).json()['status'], 'missing')
        self.user_reply = httpx.Response(200, json={**PROFILE, 'revision': 2})
        saved = self.latest().json()
        self.assertEqual(saved['status'], 'stale')
        self.assertEqual(saved['profile_revision'], 1)
        self.assertEqual(saved['current_profile_revision'], 2)
        self.assertEqual(self.generate().status_code, 200)
        self.assertEqual(self.latest().json()['status'], 'fresh')

    def test_failed_generation_preserves_previous_selection(self):
        self.assertEqual(self.generate().status_code, 200)
        previous = self.latest().json()
        self.ai_reply = httpx.ConnectError('private')
        self.assertEqual(self.generate().status_code, 503)
        self.assertEqual(self.latest().json(), previous)

    def test_storage_failure_is_controlled_and_releases_slot(self):
        self.assertEqual(self.generate().status_code, 200)
        previous = self.latest().json()
        with patch.object(self.store, 'save', side_effect=sqlite3.OperationalError('private path')):
            result = self.generate()
            self.assertEqual(result.status_code, 503)
            self.assertNotIn('private', result.text)
        self.assertEqual(self.latest().json(), previous)
        self.assertEqual(self.generate().status_code, 200)
        with patch.object(self.store, 'latest', side_effect=sqlite3.OperationalError('private path')):
            self.assertEqual(self.latest().status_code, 503)

    def test_explicit_duration_request_rejected_before_inference(self):
        for prompt in ('Quero filmes com menos de 90 minutos', 'Under 90 minutes'):
            self.calls.clear()
            response = self.generate(json={'prompt': prompt})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(len(self.calls), 1)  # Session validation only.

    def test_invented_exclusion_is_rejected(self):
        self.intent['avoids'] = ['Drama']
        self.assertEqual(self.generate(json={'prompt': 'Quero uma comédia'}).status_code, 502)

    def test_exclusion_vocabulary_is_grounded_in_negative_fragment(self):
        self.assertEqual(explicit_exclusion_genres('Quero uma comédia dos anos 90'), [])
        self.assertEqual(explicit_exclusion_genres('Quero comédia sem romance'), ['Romance'])
        self.assertEqual(explicit_exclusion_genres('horror without comedy'), ['Comedy'])

    def test_health(self):
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})
        self.assertEqual(self.calls, [])

    def test_missing_session_rejected_without_dependency_requests(self):
        response = self.client.post("/api/recommendations/generate")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.calls, [])

    def test_expired_session_stops_before_catalog(self):
        self.user_reply = httpx.Response(401, json={"error": "not logged in"})
        self.assertEqual(self.generate().status_code, 401)
        self.assertEqual(len(self.calls), 1)

    def test_profile_drives_catalog_request_and_explanation(self):
        response = self.generate(json={"limit": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["method"], "local_llm")
        result = response.json()["recommendations"][0]
        self.assertEqual(result["id"], 1)
        self.assertEqual(result["reason"], "Matches your selected genres: Adventure, Comedy. Year listed in the catalogue: 1995. Dataset average rating: 3.9/5.")
        self.assertEqual(self.calls[0].headers["cookie"], "session=test-session")
        self.assertNotIn("cookie", self.calls[1].headers)
        self.assertEqual(dict(self.calls[1].url.params), {
            "likes": "Comedy, Adventure", "avoids": "horror", "limit": "20",
        })
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_explanation_uses_selected_catalogue_movie(self):
        self.catalog_reply = httpx.Response(200, json=[{**MOVIE, 'title': 'Trailer Park Boys (1999)', 'genres': ['Comedy']}])
        response = self.generate()
        self.assertEqual(response.status_code, 200)
        reason = response.json()['recommendations'][0]['reason']
        self.assertIn('1999', reason)
        self.assertNotIn('1995', reason)
        self.assertIn('Comedy', reason)
        self.assertNotIn('Adventure', reason)
        schema = json.loads(self.calls[-1].content)['response_format']['schema']
        self.assertNotIn('reason', schema['$defs']['Pick']['properties'])

    def test_missing_year_and_rating_are_not_invented(self):
        self.user_reply = httpx.Response(200, json={**PROFILE, 'likes_genres': None})
        self.catalog_reply = httpx.Response(200, json=[{**MOVIE, 'title': 'Unknown year', 'genres': [], 'rating': 0.0}])
        self.assertEqual(self.generate().json()['recommendations'][0]['reason'], 'Selected by AI from the catalogue.')

    def test_no_body_uses_default_limit(self):
        self.assertEqual(self.generate().status_code, 200)
        self.assertEqual(self.calls[1].url.params["limit"], "20")

    def test_no_preferences_keeps_exclusions_and_explains_rating(self):
        self.user_reply = httpx.Response(200, json={**PROFILE, "likes_genres": None})
        response = self.generate()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[1].url.params["likes"], "")
        self.assertEqual(self.calls[1].url.params["avoids"], "horror")
        self.assertTrue(response.json()["recommendations"][0]["reason"])

    def test_empty_catalog_result_is_success_not_fallback(self):
        self.catalog_reply = httpx.Response(200, json=[])
        response = self.generate()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["recommendations"], [])
        self.assertEqual(len(self.calls), 2)

    def test_limit_validation_and_rejection_of_client_identity_or_preferences(self):
        bodies = [{"limit": value} for value in (0, -1, 11, "5", True, 1.5)]
        bodies += [{"username": "other"}, {"likes": "Comedy"}, {"prompt": "x" * 501}]
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(self.generate(json=body).status_code, 422)
        self.assertTrue(all(call.url.path == "/api/user/me" for call in self.calls))

    def test_dependency_network_failures_and_timeouts(self):
        for service in ("user", "catalog"):
            for failure, expected in ((httpx.ConnectError("connection details"), 503),
                                      (httpx.ReadTimeout("timeout details"), 504)):
                with self.subTest(service=service, failure=type(failure)):
                    self.user_reply = httpx.Response(200, json=PROFILE)
                    self.catalog_reply = httpx.Response(200, json=[MOVIE])
                    setattr(self, service + "_reply", failure)
                    response = self.generate()
                    self.assertEqual(response.status_code, expected)
                    self.assertNotIn("details", response.text)

    def test_dependency_http_errors_are_not_exposed(self):
        for service in ("user", "catalog"):
            self.user_reply = httpx.Response(200, json=PROFILE)
            self.catalog_reply = httpx.Response(200, json=[MOVIE])
            setattr(self, service + "_reply", httpx.Response(500, text="private traceback"))
            response = self.generate()
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("private traceback", response.text)

    def test_invalid_dependency_payloads(self):
        for service in ("user", "catalog"):
            for reply in (httpx.Response(200, text="not json"), httpx.Response(200, json={})):
                with self.subTest(service=service, content=reply.text):
                    self.user_reply = httpx.Response(200, json=PROFILE)
                    self.catalog_reply = httpx.Response(200, json=[MOVIE])
                    setattr(self, service + "_reply", reply)
                    self.assertEqual(self.generate().status_code, 502)

    def test_prompt_filters_apply_before_catalog_and_exclusions_win(self):
        self.intent.update(genres=['Horror'], year_from=1980, year_to=1989)
        self.catalog_reply = httpx.Response(200, json=[])
        response = self.generate(json={'prompt': 'Terror dos anos 80'})
        self.assertEqual(response.status_code, 200)
        params = self.calls[2].url.params
        self.assertEqual(params['likes'], 'Horror')
        self.assertEqual(params['avoids'], 'horror')
        self.assertEqual(params['year_from'], '1980')
        self.assertEqual(params['year_to'], '1989')
        self.assertNotIn('cookie', self.calls[1].headers)
        self.assertNotIn('test-session', self.calls[1].content.decode())

    def test_rejects_unsupported_constraints(self):
        self.intent['unsupported'] = True
        self.assertEqual(self.generate(json={'prompt': 'A movie directed by Spielberg'}).status_code, 422)
        self.assertEqual(len(self.calls), 2)

    def test_profile_years_cannot_be_overridden(self):
        self.user_reply = httpx.Response(200, json={**PROFILE, 'era_from': 2000})
        self.intent.update(year_to=1989)
        self.assertEqual(self.generate(json={'prompt': '80s movies'}).json()['recommendations'], [])
        self.assertEqual(len(self.calls), 2)

    def test_catalog_exclusion_is_checked_again(self):
        self.catalog_reply = httpx.Response(200, json=[{**MOVIE, 'genres': ['Comedy', 'Horror']}])
        self.assertEqual(self.generate().json()['recommendations'], [])
        self.assertEqual(len(self.calls), 2)

    def test_invalid_model_selection_and_truncated_output(self):
        for content in ('not json', '{"recommendations":[{"id":999}]}',
                        '{"recommendations":[{"id":1,"reason":" "}]}',
                        '{"recommendations":[{"id":1},{"id":1}]}'):
            self.ai_reply = httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})
            self.assertEqual(self.generate().status_code, 502)
        self.ai_reply = httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]})
        self.assertEqual(self.generate().status_code, 502)

    def test_ai_failure_never_silently_returns_rules(self):
        for reply, status in [(httpx.ReadTimeout('private'), 504), (httpx.ConnectError('private'), 503),
                              (httpx.Response(503), 503), (httpx.Response(500, text='private'), 502)]:
            self.ai_reply = reply
            response = self.generate()
            self.assertEqual(response.status_code, status)
            self.assertNotIn('private', response.text)
        self.ai_reply = None
        self.assertEqual(self.generate().status_code, 200)

    def test_busy_generation_rejected_without_catalog_or_ai(self):
        for _ in range(GENERATION_SLOTS):
            _generation_slot.acquire()
        try:
            self.assertEqual(self.generate().status_code, 503)
            self.assertEqual(len(self.calls), 0)
        finally:
            for _ in range(GENERATION_SLOTS):
                _generation_slot.release()


if __name__ == "__main__":
    unittest.main()
