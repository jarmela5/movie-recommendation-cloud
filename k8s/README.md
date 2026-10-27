# AI and agent deployment on Kubernetes

These manifests extend the working Docker Compose application to the self-hosted
cluster. Use the Ansible deployment described in [infra/README.md](../infra/README.md),
which selects published image versions and creates the internal Secret. Applying
all YAML files directly leaves the legacy `v1` image references unchanged and does
not create the Secret.

## Components and storage

| Deployment | Role | Persistent volume |
| --- | --- | --- |
| user | Accounts, sessions, preferences and profile revisions | user-data, 1Gi |
| catalog | MovieLens catalogue in SQLite | catalog-data, 1Gi |
| recommendations | AI orchestration and latest saved recommendations | recommendations-data, 1Gi |
| inference | llama.cpp with Qwen2.5-1.5B-Instruct, CPU | model-cache, 5Gi |
| agent | Periodic HTTP client, one cycle then a 300s wait | None |
| frontend | Nginx and browser interface | None |

User, Catalog, Recommendations and inference use one replica and `Recreate`:
updates stop the old pod before starting its replacement. This avoids overlapping
writers to the SQLite files and overlapping model instances. There is a short
interruption during updates. Recommendations also has an in-process generation
lock, so it must not be scaled to multiple independent replicas in this design.
The frontend retains two replicas. The agent also uses one replica with `Recreate`
to avoid overlapping cycles during updates.

The `internal-api` Secret contains key `token`, shared by User, Recommendations
and the agent. Ansible generates it only on first installation and preserves it
on later runs. The value is neither committed nor logged. Kubernetes Secrets are
not a substitute for cluster access control or encryption at rest. Internal routes
are not exposed by Nginx; inference has only a ClusterIP Service.

The Recommendations database is created at `/app/data/recommendations.sqlite3`
in its pod, backed by `recommendations-data`. It is not a database file committed
to Git. The databases already present in local Docker volumes are **not migrated**
by this deployment: a new cluster starts with empty user/recommendation databases.

`local-path` stores files on the selected node's disk and ties the volume to that
node. Pod recreation preserves the data, but losing/replacing the VM or deleting
the PVC can lose it. This is not replicated storage or a backup. Do not run
`terraform destroy`, delete the namespace or delete PVCs to test persistence.

## Capacity and first startup

Inference requests 2 CPUs and 2Gi memory, with limits of 4 CPUs and 3Gi. A worker
must have enough **allocatable** resources after system and other pod reservations;
the configured four-CPU workers provide the minimum capacity for the CPU-based
model. The cluster sizing is one 2-vCPU master plus three 4-vCPU workers (12 worker
vCPUs, 14 vCPUs including the master), staying below the project's 16-vCPU quota.
The server accepts four parallel generations. The Recommendations service has no
additional application-level waiting queue, so requests received while all four
slots are active return `503` immediately rather than waiting indefinitely.
The llama.cpp server uses a total context size of 32768 because its context is
shared across the four model slots; this provides up to 8192 context tokens per
slot and avoids the prompt-size limitations of the previous configuration.
`infra/gcp/terraform/ai.tfvars.example` shows separate master and worker sizing.
Review `terraform plan` and costs before changing an existing cluster;
VM replacement would also affect local-path data. No resize is performed by Ansible.

The pinned llama.cpp image downloads the GGUF model on first startup. The worker
needs outbound access to GHCR and Hugging Face. A startup probe allows up to roughly
30 minutes for model download/loading, and readiness waits for `/health` to succeed.
Later starts reuse `model-cache`. The Nginx recommendation route waits up to 210s,
as in Compose. The model timeout is 90s per inference call.

## Publish images

Run from the repository root after committing the intended source. Replace the
prefix with a registry namespace where you can publish; images must be readable
by the cluster (these manifests assume public application images).

```bash
export APP_IMAGE_PREFIX=YOUR_DOCKERHUB_USER
export APP_IMAGE_TAG=$(git rev-parse --short=12 HEAD)
docker login
docker buildx build --platform linux/amd64 -t "$APP_IMAGE_PREFIX/user:$APP_IMAGE_TAG" --push ./user
docker buildx build --platform linux/amd64 -t "$APP_IMAGE_PREFIX/catalog:$APP_IMAGE_TAG" --push ./services/catalog
docker buildx build --platform linux/amd64 -t "$APP_IMAGE_PREFIX/recommendations:$APP_IMAGE_TAG" --push ./services/recommendations
docker buildx build --platform linux/amd64 -t "$APP_IMAGE_PREFIX/frontend:$APP_IMAGE_TAG" --push ./frontend
```

GCP's configured N1 machines use amd64; explicitly selecting the platform also
works when building from an Apple Silicon Mac. The agent reuses the Recommendations
image. Do not overwrite a published tag; use a new tag for every source version.
The llama.cpp image is pulled separately using its pinned digest.

The public [jarmela Docker Hub profile](https://hub.docker.com/u/jarmela) lists
the available repositories and tags. Check the `user`, `catalog`,
`recommendations` and `frontend` repositories before deploying and select the
tag matching the intended source version. The recorded functional validation used
`jarmela/*:v2`, with the agent using `jarmela/recommendations:v2`. Docker Hub provides the built images; the source
Dockerfiles are maintained in this repository.

From the tools container, in `/workspace/infra/gcp`, with inventory configured:

```bash
ansible-playbook ansible/ansible-start-deployment.yml \
  -e app_image_prefix=YOUR_DOCKERHUB_USER \
  -e app_image_tag=YOUR_PUBLISHED_TAG
```

This installs storage, creates/reuses the Secret, waits for inference, deploys the
application, reloads frontend pods when its Nginx configuration changes, and starts
the agent. No model download or Secret creation is performed by the syntax check.

## Validate without deploying

With Ansible and PyYAML installed (both available in the infrastructure tools image),
from `/workspace`:

```bash
python3 k8s/tests/test_manifests.py
ansible-playbook -i localhost, infra/gcp/ansible/ansible-start-deployment.yml --syntax-check
```

These checks validate configuration relationships and syntax. They do not prove
that a cloud cluster has capacity, registry access, storage or a working model.

## Verify on the cluster

Run these commands on the master with `KUBECONFIG=/etc/kubernetes/admin.conf`,
or on a computer with the correct cluster kubeconfig. Do not share the admin
kubeconfig or the Secret's contents.

```bash
kubectl get nodes
kubectl -n movie-app get pods,svc,pvc
kubectl -n movie-app rollout status deployment/inference --timeout=1900s
kubectl -n movie-app logs deployment/inference --tail=50
kubectl -n movie-app logs deployment/agent --tail=50
kubectl -n movie-app get deployment recommendations -o jsonpath='{.spec.template.spec.containers[0].image}'
```

Expected: pods Running/Ready, all four data/cache PVCs Bound, the new published
image tag, and agent logs with `Cycle complete: generated=... skipped=... failed=0`.
Agent rollout readiness only confirms process startup: its cycle logs are needed
to confirm it can actually refresh profiles. It retries dependency failures on the
next cycle. If a pod stays Pending, inspect `kubectl -n movie-app describe pod NAME`
for insufficient CPU, memory or storage events.

Use the website at `http://WORKER_EXTERNAL_IP:30080` with a **test account**:

1. Save genre preferences and generate recommendations. Confirm real catalogue
   titles, explanations and excluded genres being respected.
2. Reload the page: saved recommendations should still appear.
3. Change the profile, then wait for an agent cycle and use **Check saved** (or
   reload). The interval is 300 seconds **after the previous cycle finishes**, not
   a promise of an update within exactly five minutes. Check the agent logs.
4. After saving a result, restart only Recommendations:

   ```bash
   kubectl -n movie-app rollout restart deployment/recommendations
   kubectl -n movie-app rollout status deployment/recommendations --timeout=300s
   ```

   Reload: the same saved result must remain available from SQLite.
5. Log out and confirm personal recommendations require login. Verify catalogue,
   profile editing, Prometheus targets and Grafana still work.

On the master, an authenticated API test can also use the website through
`kubectl -n movie-app port-forward service/frontend 8080:80` and an SSH tunnel if
needed. Never use a cloud test to modify your normal local account's preferences.

## Recorded functional validation

The team recorded the following GCP validation on 18 October 2026 for the
integration committed as `a8e0ab6` and subsequently merged into master:

- Kubernetes v1.28.15: one control plane and three workers Ready; application
  pods healthy, four PVCs Bound, NodePort frontend and monitoring available.
- Application images `jarmela/*:v2`; the agent reused Recommendations v2.
- A test account received five real catalogue films with explanations, with
  preferred and excluded genres respected, and the result was saved.
- Changing Comedy to Adventure marked the saved result stale. The agent
  refreshed it in approximately 4m20s in that observation, retaining the Horror
  exclusion. This timing is an observation, not a guaranteed refresh deadline.
- Restarting only Recommendations preserved the exact saved films, generation
  timestamp and profile revision. Page reload recovered the saved result.
- Login, profile editing, catalogue search, manual generation, saved retrieval,
  logout and private-profile access were checked; the final logout check was
  confirmed manually by the team.
- Prometheus application targets were UP and Grafana displayed real metrics.

Screenshots and test outputs were retained separately for the report/demo.
These functional checks are separate from the load measurements committed in
`a413df5`; see [their scope and limitations](../performance/README.md).
The cluster was destroyed after evidence collection to stop charges. Do not
reuse its former public IPs. Reproducing the deployment requires a new explicit
provisioning run and fresh inventory addresses.

This validates persistence across a Recommendations restart, not recovery after
VM loss, `terraform destroy`, or a full-cluster shutdown/restart experiment.
The selected advanced component is DevSecOps, documented in the root README.
