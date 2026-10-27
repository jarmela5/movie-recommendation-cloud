# Performance study

## Scope of the committed measurements

The committed results/charts were introduced with the rules-based Recommendations
service, before integration of local AI, persistence and the background agent.
Keep them as a baseline, not as measurements of the final AI application.
Rerun on the final deployment and record the commit, hardware, model, agent state
and workload. Separate generation from cached retrieval (`/api/recommendations/latest`)
and registration metrics. AI generation currently permits four concurrent
requests and has no application-level waiting queue; requests beyond
that active capacity return 503 and must be reported, not counted as successful
recommendation throughput. Run load tests with disposable
User/Recommendations data: the scenarios create accounts which the agent scans.
The Kubernetes target uses one 2-vCPU master, three 4-vCPU workers and an
Inference Pod limited to 4 CPUs/3GiB with `--parallel 4`, `--threads 4` and
`--ctx-size 32768`. This is a 14-vCPU deployment under the 16-vCPU project quota;
the 12 worker vCPUs are shared by the cluster and are not combined for one Pod.

The committed Recommendations workload measures generation, not cached retrieval.
Its HTTP metrics include both account registration and generation, including failed
requests. The throughput chart therefore shows **all HTTP requests per second**,
not successful AI generations per second. Aggregate latency percentiles likewise
mix registration, successful generation and errors. A fast rejection can reduce
p95 without making inference faster. The error chart is also an aggregate: account
registrations dilute the generation-only failure rate.

Recommendations accepts one generation at a time per process and returns 503 when
busy. The saved summaries do not break failures down by status code, so they do
not prove that every failure was a busy response. k6 also uses its default request
timeout in the current scenario, while the backend may spend longer on inference.
The default 30-second graceful stop can leave slow iterations incomplete. These
limits must be considered when interpreting the measurements.

### Recorded generation checks

These counts come directly from `root_group.checks["recommendations succeeded"]`
in the committed JSON files. Success here means HTTP 200, not a separate check of
movie quality or response contents.

| Virtual users | Generation HTTP 200 | Failed generation checks | Generation failure rate |
| --- | ---: | ---: | ---: |
| 1 | 3 | 0 | 0.00% |
| 5 | 5 | 215 | 97.73% |
| 20 | 5 | 909 | 99.45% |
| 50 | 4 | 804 | 99.50% |
| 100 | 2 | 694 | 99.71% |

The one-user run completed three generation checks successfully. The concurrent
runs show substantial rejection/failure under load; they do not demonstrate high
successful AI throughput. Catalog and search recorded zero HTTP failures in these
runs, but their latency/throughput figures describe this workload only.

For a future measurement, tag registration and generation separately, record
successful-generation throughput and status-code counts, and benchmark
`GET /api/recommendations/latest` separately. Retain run metadata (source commit,
image digests, VM types, model, agent state, timeouts and test location). This is
methodological guidance, not a claim that those extra measurements already exist.
No measurements were rerun for this documentation correction.

Run load tests only against a current, explicitly provisioned environment with
disposable User/Recommendations data: the scenarios create accounts that the
agent scans. Do not reuse the destroyed validation cluster's former IPs.

This directory contains the reproducible load tests required for the project report.
Run these tests from a host with Docker and a running Docker daemon. The
infrastructure management container is intended for Terraform, Ansible and
kubectl commands; it does not include the Docker CLI or Docker socket.

The tests run against the URL in `BASE_URL` and measure the complete request path through Nginx and the backend services. The default is `http://localhost:8080` for Docker Compose; for Kubernetes, set `BASE_URL` to the public NodePort URL.

## Start the application

From the repository root on the Docker-enabled host:

    docker compose up -d --build

## Run a test with the k6 Docker image

The k6 container is run with the host user ID so generated files remain writable.
The scripts use host networking; for a local target, the Docker installation must
support and enable that mode (including Docker Desktop configuration where needed).

    docker run --rm --network host --user "$(id -u):$(id -g)" \
      -v "$PWD:/work" -w /work grafana/k6 run \
      --vus 5 --duration 60s \
      --summary-export performance/results/catalog-5vus.json \
      performance/k6/catalog.js

Run the complete set of scenarios:

    docker run --rm --network host --user "$(id -u):$(id -g)" \
      -v "$PWD:/work" -w /work grafana/k6 run \
      --vus 5 --duration 60s \
      --summary-export performance/results/catalog-5vus.json \
      performance/k6/catalog.js

    docker run --rm --network host --user "$(id -u):$(id -g)" \
      -v "$PWD:/work" -w /work grafana/k6 run \
      --vus 5 --duration 60s \
      --summary-export performance/results/search-5vus.json \
      performance/k6/search.js

    docker run --rm --network host --user "$(id -u):$(id -g)" \
      -v "$PWD:/work" -w /work grafana/k6 run \
      --vus 5 --duration 60s \
      --summary-export performance/results/recommendations-5vus.json \
      performance/k6/recommendations.js

The complete study uses 1, 5, 20, 50, and 100 VUs for catalog, search, and recommendations.
For the AI capacity boundary, also run 4 and 5 VUs: 4 exercises the active
slots, and 5 should expose immediate capacity-full `503` responses. With only
4 CPUs available to inference, long model
latencies and client timeouts are expected under load and must be reported.

## Run the complete study

From the repository root:

    docker compose up -d --build
    chmod +x performance/run_all.sh
    performance/run_all.sh

The script runs catalog, search, and recommendations with 1, 5, 20, 50, and 100 virtual users. Each run lasts 60 seconds.

For a shorter validation run, edit DURATION in performance/run_all.sh to 10s. The script uses the k6 Docker image and a Python Docker image. It writes JSON summaries to performance/results/ and generates the charts automatically. No local Python installation is required.

## Run against Kubernetes on GCP

Do not start the local Compose application when measuring Kubernetes. First, in
the infrastructure tools container, verify that the cluster is ready:

    ansible master -b -m shell -a \
      'KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes -o wide'

The same Ansible pattern can be used to inspect the application resources:

    ansible master -b -m shell -a \
      'KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n movie-app get pods,svc,pvc'

Then leave the tools container. On the Docker-enabled host, obtain a worker's
public IP from the GCP console, `gcloud compute instances list` or `gcphosts`.
The frontend is exposed through NodePort `30080`. Run the complete study outside
the tools container against the public GCP endpoint:

    export BASE_URL=http://WORKER_EXTERNAL_IP:30080
    ./performance/run_all.sh

The scripts accept `BASE_URL` through `__ENV.BASE_URL`; without it they retain
the local Compose default. The public NodePort measurement includes the network path to GCP. A
`kubectl port-forward` is useful for debugging but is a different measurement
setup and should be identified as such.

Run a short smoke test before the full study:

    export BASE_URL=http://WORKER_EXTERNAL_IP:30080
    docker run --rm --network host -e BASE_URL="$BASE_URL" \
      -v "$PWD:/work" -w /work grafana/k6 run \
      --vus 1 --duration 10s performance/k6/catalog.js

The recommendation scenario creates disposable test users. Keep the agent state
and any resulting 503 responses in the measurements: the inference deployment
uses four concurrent model slots and no application-level waiting queue, so
capacity-full failures are part of the real capacity and must not be counted as
successful throughput.
## Generate charts

After the JSON files have been generated, run the chart generator in a Python Docker container:

    docker run --rm --user "$(id -u):$(id -g)" \
      -v "$PWD:/work" -w /work python:3.13-slim \
      python performance/generate_charts.py

The command writes:

- performance/charts/summary.csv
- performance/charts/latency-p95.svg
- performance/charts/throughput.svg
- performance/charts/errors.svg

The script ignores empty or invalid JSON files.

## What the tests and charts show

The study contains three k6 scenarios:

- **Catalog**: measures the latency of listing movies through the frontend gateway.
- **Search**: measures movie search latency and verifies that the response is a JSON array.
- **Recommendations**: registers one user per virtual user, authenticates the session, and measures recommendation generation.

The tests are repeated with increasing numbers of virtual users. This shows how throughput and latency change as concurrency increases, and whether the system starts returning errors under load.

The generated charts show:

- **latency-p95.svg**: aggregate HTTP p95 for each scenario and VU level. For
  Recommendations, this mixes registration, successful generation and failures.
- **throughput.svg**: all HTTP requests per second, including failed requests and
  registration. It does not measure successful AI generation throughput.
- **errors.svg**: aggregate HTTP failure percentage. Generation-only failure rates
  are listed above; overload rejection is part of the measured behaviour.

The charts support the evaluation of the backend request path through Nginx and the services. They do not directly measure browser rendering, background-agent execution, or infrastructure resource usage.
## Metrics to report

For each scenario and concurrency level, record:

- request throughput;
- average latency;
- p95 and p99 latency;
- error rate;
- total requests.

Use the resulting CSV and SVG files in the Evaluation section of the report. Explain when latency starts increasing and whether errors appear as concurrency grows.

## Clean up

Stop the application after the measurements:

    docker compose down
