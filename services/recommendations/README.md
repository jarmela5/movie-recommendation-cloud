# Recommendations — local AI integration

Manual recommendations now use the published **Qwen2.5-1.5B-Instruct** model,
quantized as Q4_K_M, through a local **llama.cpp** server. No training or fine-tuning
is performed. Model: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF.
The CPU configuration is intended for local development on modest hardware; it is
not a performance benchmark or a production sizing recommendation.

The service implements on-demand AI recommendations and stores the latest
successful result per user. A separate periodic worker now prepares missing or
stale results automatically. The frontend retrieves saved results on entry after
login. The same setup is implemented and was functionally validated on self-hosted
Kubernetes; see [the Kubernetes guide](../../k8s/README.md).

## Request flow

1. Browser sends `POST /api/recommendations/generate` through Nginx with its session.
2. Recommendations validates that session through User and obtains the profile.
3. If `prompt` is supplied, the model extracts genres, exclusions and year bounds.
4. Catalog filters the local MovieLens dataset and ranks 20–30 candidates by genre
   overlap and rating. Years are extracted from the final `(YYYY)` in titles.
5. The model selects candidate IDs, using
   the request plus profile biography/tags as soft context.
6. Recommendations validates JSON, count, distinct IDs and membership of the
   candidate set. Actual movie fields come from Catalog, not generated metadata.
7. The backend builds English explanations from matching genres, the year in the
   catalogue title and the dataset rating. The page renders them with escaped text.

Recommendations never reads the CSV or User SQLite directly. Cookies go only to
User; inference receives no session cookie, username, password or email. The
request and relevant profile text are processed by the local model. Internet is
needed to obtain the image and model initially, not for remote inference.

## Contract and filtering

```json
{"limit": 5, "prompt": "Quero uma comédia dos anos 90"}
```

- `limit`: integer 1–10, default 5. Small bounds limit CPU work and context size.
- `prompt`: optional string up to 500 characters. Empty means use the profile.
- Client-supplied identity/preferences and all other fields are rejected.
- Explicit requested genres replace profile preferred genres for this request.
- Profile exclusions always remain; explicit request exclusions are added.
- Profile years and requested years are intersected; contradictions yield `[]`.
- With year filters, movies with unknown years are excluded.
- No preferences means a general candidate pool, still respecting exclusions.
- No eligible candidates means HTTP 200 with an empty list; there is no selection
  inference call in that case.
- Runtime, cast, director and plot constraints are unsupported because those facts
  are absent from the dataset. Requests identified as requiring them receive 422.
  The profile's `max_runtime` is not enforced by this version.

Response includes `method: "local_llm"`, `model: "movie-qwen"`,
`applied_filters`, and `recommendations` (the existing movie fields plus `reason`).
`applied_filters` exposes the interpretation for inspection. Semantic extraction
can still be imperfect. Explanations are deterministic catalogue facts, not a
description of the model’s internal reasoning.
The backend enforces the resulting genre/year restrictions and validates IDs.
Request exclusions are restricted to genre names mentioned after an explicit
negative expression (such as "sem" or "without") in English or Portuguese; obvious duration keywords are rejected without relying on the model.
There is no silent fallback to genre-rule recommendations when inference fails.

## Stored recommendations

Every successful generation (including an empty selection) atomically replaces the
latest result for the authenticated user. Failed inference leaves the old selection
untouched. If saving fails, generation responds with a controlled 503, not a false
success. The generation response format remains unchanged for the existing UI.

SQLite belongs exclusively to Recommendations; it never opens User's database.
`RECOMMENDATIONS_DB_PATH` defaults to `/app/data/recommendations.sqlite3`, mounted
on `recommendations-data` in the root Compose. Stored fields are user ID, User
profile revision, UTC generation timestamp, original prompt, requested limit and
complete result JSON (including films, explanations, model and applied filters).
No session cookie or password is stored. Only the latest selection is retained,
not a history. A write from an older profile revision cannot overwrite a newer one.

`GET /api/recommendations/latest` validates the session with User and returns:

```json
{
  "status": "fresh",
  "current_profile_revision": 2,
  "profile_revision": 2,
  "generated_at": "2026-10-17T12:00:00+00:00",
  "prompt": "",
  "requested_limit": 5,
  "result": {"method": "local_llm", "model": "movie-qwen", "applied_filters": {}, "recommendations": []}
}
```

- `missing`: no saved row; result and saved metadata are null.
- `fresh`: saved revision matches the current User profile revision.
- `stale`: the revisions differ; the old result is returned with that warning.
- A saved empty list is a successful selection, not `missing`.
- Invalid/missing sessions receive 401. The session determines the user; client
  query parameters cannot select someone else's data. Responses use `no-store`.
- Reading calls User only, not Catalog or inference. Availability of User is still
  required to authenticate and determine staleness.

`fresh` currently means profile-version agreement, not age-based freshness or
agreement with a new dataset/model version. Profile changes during generation
produce a result tagged with the original revision, so a later read marks it stale.
Manual prompts are stored but not written into the user profile. The agent now
regenerates missing/stale results periodically. The page retrieves the last saved
result on entry and labels stale selections explicitly. Profiles without stored results are populated on a
successful agent cycle. User IDs assume the corresponding User database is retained; resetting
only User data while keeping recommendations would break that identity mapping.

This SQLite deployment supports one Recommendations process. Compose uses a named
volume; Kubernetes uses one replica, the `Recreate` strategy and a 1Gi
`recommendations-data` PVC mounted at `/app/data`. The local-path volume survives
pod recreation, but is tied to its node and is not replicated or backed up.

To inspect without changing the UI, while logged in open the browser console:

```js
fetch('/api/recommendations/latest')
  .then(async r => console.log(r.status, await r.json()));
```

Check `missing` on a new account, generate once for `fresh`, then edit the profile
and read again for `stale`. Generate again to refresh it. Restart Recommendations
and repeat the read to confirm the result survives.

## Start and test through the page

From the repository root, with Docker Desktop running:

```sh
docker compose up -d --build
docker compose logs --tail=30 inference
docker compose exec -T inference curl -fsS http://localhost:8080/health
```

The first start downloads approximately 1.1 GB of weights. Wait for
`{"status":"ok"}`; generation can take tens of seconds on CPU. The image is
pinned by digest in Compose; the named Hugging Face file is downloaded to the
`model-cache` volume. The upstream model revision is not pinned in the current configuration.
`docker compose down` preserves named volumes; `down -v` removes the model
cache, normal User data and saved recommendations.

Open http://localhost:8080/recommendations.html and log in with a test account.
Set Comedy as preferred and Horror as excluded, with no profile year restriction:

1. Leave the request empty: get up to five Comedy films, with no Horror.
2. Ask “Quero uma comédia dos anos 90”: inspect title years (1990–1999).
3. Ask “Quero filmes de terror”: expect no matches because Horror remains excluded.
4. Ask “Quero filmes com menos de 90 minutos”: expect unsupported-criteria feedback.
5. Stop inference, generate, then restart it: see a controlled error, then retry.
6. Log out in another tab and generate: expect the login prompt and cleared cards.

For response details, run in the browser console while logged in:

```js
fetch('/api/recommendations/generate', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({limit: 3, prompt: 'Quero uma comédia dos anos 90'})
}).then(async r => console.log(r.status, await r.json()));
```

Without a session:

```sh
curl -i -X POST http://localhost:8080/api/recommendations/generate
```

Expect 401. Recommendations `/healthz` is process liveness only. Inference's
internal `/health` reports model readiness; neither service publishes a host port.

## Failure handling and configuration

| Status | Meaning |
| --- | --- |
| 401 | Missing or invalid session. |
| 422 | Invalid body or unsupported request criteria. |
| 502 | Dependency error, invalid JSON, truncated or invalid model selection. |
| 503 | Dependency unavailable, model loading, or all generation slots are occupied. |
| 504 | Dependency timeout. |

Four generations are accepted concurrently **per Recommendations process**.
There is no application-level waiting queue: requests arriving when all four
slots are occupied are rejected immediately with `503`.
This is not a distributed lock across replicas. The inference server
also runs four slots with a total context size of 32768 and four CPU threads;
Nginx allows 210 seconds for the response. The Kubernetes inference Pod requests
2 CPUs and 2GiB and is limited to 4 CPUs and 3GiB, so high concurrency can
increase latency and produce 503 responses when all four slots are occupied.

| Variable | Default |
| --- | --- |
| USER_SERVICE_URL | http://user:8000 |
| CATALOG_SERVICE_URL | http://catalog:8000 |
| REQUEST_TIMEOUT_SECONDS | 5 |
| INFERENCE_SERVICE_URL | http://inference:8080 |
| INFERENCE_MODEL | movie-qwen |
| INFERENCE_TIMEOUT_SECONDS | 90 per inference HTTP network operation |

## Automated checks

```sh
node --test frontend/tests/*.test.js
docker compose exec -T recommendations python -m unittest discover -s tests -v
docker compose exec -T catalog python -m unittest discover -s tests -v
```

These check request/response validation, errors, authentication, exclusions, year
filtering, loading states, safe rendering and free-text submission with simulated
inference. They do not measure model quality.

Integration with real User, Catalog and Nginx, but deterministic inference stub:

```sh
docker compose -p g36-ai-tests -f services/recommendations/tests/compose.integration.yml up -d --build
docker compose -p g36-ai-tests -f services/recommendations/tests/compose.integration.yml restart frontend
docker compose -p g36-ai-tests -f services/recommendations/tests/compose.integration.yml exec -T recommendations python tests/integration.py
docker compose -p g36-ai-tests -f services/recommendations/tests/compose.integration.yml down -v
```

Optional **real-model** smoke checks: first start inference with project name
`g36` and wait for its health endpoint. Then:

```sh
docker compose -p g36 up -d inference
docker compose -p g36-ai-smoke -f services/recommendations/tests/compose.ai.yml up -d --build
docker compose -p g36-ai-smoke -f services/recommendations/tests/compose.ai.yml restart frontend
docker compose -p g36-ai-smoke -f services/recommendations/tests/compose.ai.yml exec -T recommendations python tests/ai_smoke.py
docker compose -p g36-ai-smoke -f services/recommendations/tests/compose.ai.yml down
```

Both test stacks keep User data in a disposable RAM filesystem, separate from
normal accounts. Always run their cleanup command, even after failures. The real
model test shares the running `g36` inference container (do not generate from the
normal application concurrently) and exposes its test UI only on localhost:18081.
It checks profile-based selection, Portuguese genre/year extraction, exclusions,
real catalogue IDs and an unsupported runtime request. Passing these examples is
not proof that every possible natural-language request will be interpreted well.


## Recorded validation

The combined implementation validation recorded on 17 October 2026 passed
13 Catalog tests, 36 Recommendations/agent/storage tests and 16 frontend tests.
It also passed deterministic integration, persistence across container recreation,
five real-model smoke scenarios and real-model agent integration. These are
recorded results for that implementation, not a claim of a new test run whenever
this README is read. Commands above allow the checks to be repeated.

The real-model agent checks included repeated cycles without regenerating fresh
results. An initial attempt encountered a transient 503; the subsequent isolated
run passed. Unit tests separately cover worker retry behaviour.

An earlier visual check found an invented year in a model-written explanation.
The implementation now asks the model for IDs only and builds explanations from
catalogue facts. Regression tests cover the correct year, matching genres and
missing metadata.

The later GCP Kubernetes functional validation is recorded in the
[Kubernetes guide](../../k8s/README.md#recorded-functional-validation).
Performance measurements and their limitations are documented separately in
[the performance guide](../../performance/README.md). Functional tests do not
establish model quality for arbitrary prompts or high-load capacity.

## Persistence integration test

Use this isolated environment, not the normal application. Inference is simulated;
real User, Catalog, Nginx and SQLite are used. User data is temporary, and the named
recommendations test volume is separate from normal data. Run from the repo root:

```sh
docker compose -p g36-storage-test -f services/recommendations/tests/compose.integration.yml up -d --build
docker compose -p g36-storage-test -f services/recommendations/tests/compose.integration.yml restart frontend
docker compose -p g36-storage-test -f services/recommendations/tests/compose.integration.yml exec -T recommendations python tests/persistence_integration.py prepare
docker compose -p g36-storage-test -f services/recommendations/tests/compose.integration.yml up -d --no-deps --force-recreate recommendations
docker compose -p g36-storage-test -f services/recommendations/tests/compose.integration.yml restart frontend
docker compose -p g36-storage-test -f services/recommendations/tests/compose.integration.yml exec -T recommendations python tests/persistence_integration.py verify
docker compose -p g36-storage-test -f services/recommendations/tests/compose.integration.yml down -v
```

The final `down -v` applies ONLY to the named disposable test project. It removes
its recommendations test volume; never use it on the normal project to preserve
data. Clean this test project after failures as well.

## Background agent

`agent.py` runs as the separate Compose service `agent`, using the same image as
Recommendations, but no database volume or published port. Compose waits for
User and Recommendations health checks before starting the worker. It starts a scan on
startup, then waits `AGENT_INTERVAL_SECONDS` (default 300) after each completed
scan. Requests are sequential, so cycles never overlap within a worker.

1. Agent reads paginated user IDs/revisions from `GET /internal/profiles` on User
   (50 per page, ordered by ID). Private profiles are included for personalised
   processing; they remain private to other users.
2. It calls `POST /internal/recommendations/{user_id}/refresh` for each profile.
3. Recommendations fetches the current profile from User's internal endpoint,
   checks its own SQLite snapshot and skips results already at that revision.
4. Missing/stale results use the normal Catalog + AI generation path, limit 5,
   no free-text request. Results are saved before reporting success.

The same generation capacity protects interactive and automatic requests. Four
requests can run concurrently and there is no application-level waiting queue;
when all four slots are occupied, the service returns `503`. Failures are retried next cycle and do not
erase saved results. The queue is not distributed across replicas: run one
worker and one Recommendations process for this local SQLite setup.

Freshness means profile revision, not age. Empty successful results count as
fresh. A manual result for the current revision also counts as fresh and is not
overwritten by the agent. A profile edited during generation leaves a stale
snapshot, which the next cycle refreshes. No user sessions or cookies are needed;
this can run while users are logged out. There is no guarantee a new account has
results immediately: it must first be scanned and generated successfully.

### Internal authentication and local setup

Run `python3 scripts/setup_agent.py` from the repository root before Compose.
It creates a random `INTERNAL_API_TOKEN` in the Git-ignored `.env`, preserving
existing settings. User, Recommendations and Agent receive this value through
their environment. Do not print, commit or share it. Changing it requires
recreating all three services. Without a configured token, internal endpoints
reject requests and the worker refuses to start; public session routes still work.

The internal routes are outside `/api/` and are not proxied by frontend Nginx.
They require `X-Internal-Token` even inside the Docker network. The token is never
sent to Catalog or inference. User returns only IDs/revisions in the listing and
only the needed preference fields in the detail route (no emails or credentials).
On Kubernetes, the deployment playbook creates or reuses the `internal-api`
Secret. User, Recommendations and the separate agent Deployment reference its
`token` key. The agent reuses the Recommendations image and has no database mount.

### Observe and test

```sh
# Normal application: start the configured background process.
docker compose up -d --build
docker compose logs --tail=30 agent
# Pause/resume automatic work without removing data:
docker compose stop agent
docker compose start agent
```

Logs report `generated`, `skipped`, and `failed` counts. From a logged-in browser,
read `fetch('/api/recommendations/latest').then(r => r.json()).then(console.log)`.
After editing a profile, its saved result is stale until a subsequent successful
cycle. Once generated, status becomes fresh without clicking Get recommendations.
The frontend loads saved results on entry. Use Check saved recommendations to
retrieve a newer snapshot without generating; there is no automatic polling.

Isolated verification (simulated inference, real User/Catalog/SQLite/Nginx):

```sh
docker compose -p g36-agent-test -f services/recommendations/tests/compose.integration.yml up -d --build
docker compose -p g36-agent-test -f services/recommendations/tests/compose.integration.yml exec -T recommendations python -m unittest discover -s tests -v
docker compose -p g36-agent-test -f services/recommendations/tests/compose.integration.yml exec -T recommendations python tests/integration.py
docker compose -p g36-agent-test -f services/recommendations/tests/compose.integration.yml exec -T recommendations python tests/agent_integration.py
docker compose -p g36-agent-test -f services/recommendations/tests/compose.integration.yml exec -T recommendations python agent.py --once
docker compose -p g36-agent-test -f services/recommendations/tests/compose.integration.yml down -v
```

The integration test creates a private test profile, logs out, runs the worker,
logs in and retrieves precomputed results. It also checks authentication, cache
hits and regeneration after a profile update. The CI integration job runs it too.
`--once` exits nonzero if any profile fails, useful for manual checks. The normal
loop continues after transient failures. Test fixture tokens are not credentials
for the normal application. Cleanup above removes only the disposable test volume.

## Saved recommendations in the browser

Successful login redirects to `recommendations.html`. After checking the session,
the page calls `GET /api/recommendations/latest`, without triggering inference.
Fresh results show cards, saved date and the previous request (or profile-only
context). Stale results remain visible with an explicit warning that they may no
longer match current preferences. Missing results and a saved empty selection have
different messages. Entering the page does not guarantee that a new account's
background generation has already finished.

`Check saved recommendations` retrieves the latest snapshot without a new AI call.
`Get recommendations` retains manual generation. Cache reads have a 15-second
timeout, handle expired sessions and can be retried independently of generation.
Reads and generation are serialized in the UI to avoid late responses replacing
new results. The form remains available after a saved-result fetch fails.

Manual check: log in, confirm cards appear without submitting the form, edit the
profile and return to see the stale warning, then check saved recommendations after
the agent's next successful cycle. Refresh the page to confirm stored results remain.
Run `node --test frontend/tests/*.test.js` for the 16 frontend checks covering
saved/manual flows, missing/empty/stale data, authentication, errors and safe text.
