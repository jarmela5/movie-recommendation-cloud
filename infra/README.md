# Kubernetes Deployment on GCP

This README explains how to deploy the application to a self-hosted Kubernetes cluster on Google Cloud using the Terraform, Ansible, and Kubernetes files in this repository.

## Architecture

The cluster contains:

- 1 master node
- 3 worker nodes
- Kubernetes installed with kubeadm
- Terraform for the VMs and network
- Ansible for machine configuration and Kubernetes installation
- frontend, catalog, recommendations, user, inference and agent services
- Prometheus and Grafana for monitoring

    Browser
       |
       v
    NodePort :30080
       |
       v
    frontend (nginx)
       +--> user
       +--> recommendations --> user, catalog, inference
       +--> catalog

    agent --> user, recommendations (authenticated internal HTTP)
    user / catalog / recommendations --> their own SQLite volumes
    inference --> model-cache volume
    Prometheus --> user, catalog, recommendations
    Grafana --> Prometheus

---

# 1. Before you start

Each team member needs Docker, Docker Compose, access to a GCP project with billing enabled, a Docker Hub account, an SSH key, and access to the project repository.

> **Docker Compose note:** if docker compose version returns
> unknown shorthand flag: 'd' in -d, install the Compose plugin:
>
>     sudo apt install docker-compose-v2
>
> Or install it for the current user:
>
>     mkdir -p ~/.docker/cli-plugins
>     curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
>       -o ~/.docker/cli-plugins/docker-compose
>     chmod +x ~/.docker/cli-plugins/docker-compose

The GCP infrastructure uses paid resources. Only one team member should create or destroy the cluster at a time.

> Whoever runs terraform apply is responsible for running terraform destroy at the end.

---

# 2. Management environment

The project provides a tools management container with Terraform, Ansible, kubectl, and SSH utilities.

From the project root, build it with:

    docker compose -f infra/tools/compose.yaml build tools

Enter it with:

    docker compose -f infra/tools/compose.yaml run --rm tools

The working directory inside the container is:

    /workspace/infra/gcp

Check the environment with:

    terraform version
    ansible --version
    kubectl version --client

Leave the container with:

    exit

The temporary container is removed automatically because --rm was used.

---

# 3. Local tests with Docker Compose

The root docker-compose.yml runs eight services: inference, agent, catalog, user,
recommendations, frontend, Prometheus and Grafana. Before the first start, run
`python3 scripts/setup_agent.py` to configure the internal credential.

From the project root:

    docker compose up --build

Open the application at:

    http://localhost:8080

The services communicate through their Compose service names:

    recommendations -> user:8000
    recommendations -> catalog:8000
    recommendations -> inference:8080
    agent -> user:8000, recommendations:8000
    frontend -> user
    frontend -> catalog
    frontend -> recommendations
    prometheus -> user, catalog, recommendations
    grafana -> prometheus

The user-data, catalog-data and recommendations-data volumes preserve the three
SQLite databases between restarts; model-cache preserves the model weights.

Stop the application with:

    docker compose down

Only when deliberately discarding all local application data and model weights:

    docker compose down -v

This Compose deployment is for local testing. The final GCP deployment uses the published images and the manifests in k8s/.

---

# 4. Docker images on Docker Hub

Publish the current User, Catalog, Recommendations and Frontend images with a
new immutable tag before deploying. The agent uses the Recommendations image.
Build for `linux/amd64` when targeting the configured GCP N1 machines, including
when building on an Apple Silicon Mac.

From the repository root, after committing the intended source, run:

```bash
cd /workspace
export APP_IMAGE_PREFIX=jarmela
export APP_IMAGE_TAG=$(git rev-parse --short=12 HEAD)

docker login
docker buildx build --platform linux/amd64 -t "$APP_IMAGE_PREFIX/user:$APP_IMAGE_TAG" --push ./user
docker buildx build --platform linux/amd64 -t "$APP_IMAGE_PREFIX/catalog:$APP_IMAGE_TAG" --push ./services/catalog
docker buildx build --platform linux/amd64 -t "$APP_IMAGE_PREFIX/recommendations:$APP_IMAGE_TAG" --push ./services/recommendations
docker buildx build --platform linux/amd64 -t "$APP_IMAGE_PREFIX/frontend:$APP_IMAGE_TAG" --push ./frontend
```

Use your Docker Hub namespace instead of `jarmela` when necessary. The Ansible playbook requires `app_image_prefix` and `app_image_tag`;
it replaces the legacy `jarmela/*:v1` references on the master before applying the
manifests. Do not deploy the old v1 images for the AI integration.

The currently published public images and their available tags can be checked on
the [jarmela Docker Hub profile](https://hub.docker.com/u/jarmela). Open the
`user`, `catalog`, `recommendations` and `frontend` repositories and use the
tag matching the intended source version for all four images. The recorded
functional validation used `v2`. Docker Hub contains the built
images and tags; the authoritative Dockerfiles remain in this Git repository.
Pass the selected tag explicitly when deploying from `/workspace/infra/gcp`, for example:

```bash
cd /workspace/infra/gcp
ansible-playbook ansible/ansible-start-deployment.yml \
  -e app_image_prefix="$APP_IMAGE_PREFIX" \
  -e app_image_tag="$APP_IMAGE_TAG"
```

To deploy a tag published from another machine, replace `APP_IMAGE_TAG` with the
common tag shown in the four Docker Hub repositories:

```bash
cd /workspace/infra/gcp
ansible-playbook ansible/ansible-start-deployment.yml \
  -e app_image_prefix=jarmela \
  -e app_image_tag=YOUR_PUBLISHED_TAG
```

---

# 5. Create and configure the GCP project

If you do not already have a GCP project:

1. Create a project in Google Cloud.
2. Enable billing.
3. Enable the Compute Engine API.
4. Save the Project ID.

The Project ID is used by Terraform and is different from the project display name.

## 5.1 Create Terraform credentials

In Google Cloud, open:

    IAM & Admin -> Service Accounts -> Create Service Account

Create a service account for Terraform. The Editor role may be used for this project.

Create a JSON key through:

    Service Account -> Keys -> Add key -> Create new key -> JSON

Copy the downloaded file to:

    infra/gcp/terraform/

For example:

    infra/gcp/terraform/gcp-credentials.json

Never commit this file. The .gitignore file excludes GCP credentials and Terraform state files.

---

# 6. Configure Terraform

Enter the management container and run:

    cd /workspace/infra/gcp/terraform

In terraform-gcp-variables.tf, confirm the GCP_PROJECT_ID, region, and zone values.

In terraform-gcp-provider.tf, confirm that the credentials filename matches the JSON file placed in infra/gcp/terraform/:

    credentials = file("...")

If you do not have an SSH key, create it on the host before entering the tools
container (the SSH directory is mounted read-only):

    ssh-keygen -t rsa -b 2048

The current Terraform file reads `/root/.ssh/id_rsa.pub`; Ansible reads
`/root/.ssh/id_rsa`. The tools container mounts the host SSH directory read-only.
If using another key, configure both paths consistently. The SSH username is
`ubuntu` in Terraform and must match the inventory.

---

# 7. Create the VMs with Terraform

From /workspace/infra/gcp/terraform:

    terraform init
    terraform plan -var-file=ai.tfvars.example
    terraform apply -var-file=ai.tfvars.example

Confirm with yes.

Terraform creates:

- 1 master node
- 3 worker nodes
- the required network and firewall rules

The master should have at least 2 vCPUs for kubeadm.

Get the public IP addresses with:

    terraform output

Save the four addresses.

---

# 8. Update the Ansible inventory

Public IP addresses may change after the infrastructure is recreated.

Edit infra/gcp/gcphosts:

    [control_plane]
    master ansible_host=<MASTER_IP> ansible_user=ubuntu ansible_connection=ssh

    [workers]
    worker1 ansible_host=<WORKER1_IP> ansible_user=ubuntu ansible_connection=ssh
    worker2 ansible_host=<WORKER2_IP> ansible_user=ubuntu ansible_connection=ssh
    worker3 ansible_host=<WORKER3_IP> ansible_user=ubuntu ansible_connection=ssh

    [targets:children]
    control_plane
    workers

    [osmgmt]
    localhost ansible_connection=local

Replace the placeholders with the real IP addresses and SSH username. Also check infra/gcp/ansible.cfg.

---

# 9. Test the VM connections

From /workspace/infra/gcp:

    ansible targets -m ping

All four nodes should return:

    pong

Fix SSH and Ansible configuration before continuing if any node fails.

---

# 10. Install Kubernetes with Ansible

Run the playbooks in this order:

    ansible-playbook ansible/ansible-gcp-configure-nodes.yml
    ansible-playbook ansible/ansible-k8s-install.yml
    ansible-playbook ansible/ansible-create-cluster.yml
    ansible-playbook ansible/ansible-workers-join.yml

The final cluster should contain master, worker1, worker2, and worker3.

---

# 11. Deploy the application

After Kubernetes is installed, first inspect the published images in the
[jarmela Docker Hub profile](https://hub.docker.com/u/jarmela). Open the
`user`, `catalog`, `recommendations` and `frontend` repositories and choose the
newest immutable tag that is common to all four repositories. Use the same tag
for every application image; do not use `latest` or the legacy `v1` tag.

Then, from `/workspace/infra/gcp`, run the playbook with that Docker Hub
namespace and tag:

    ansible-playbook ansible/ansible-start-deployment.yml \
      -e app_image_prefix=jarmela \
      -e app_image_tag=THE_COMMON_TAG_FROM_DOCKER_HUB

If the images were published under another Docker Hub account, replace
`jarmela` with that account name. The tag must exist in all four repositories;
the inference image is pulled separately from its pinned llama.cpp image.

This playbook:

1. creates /root/k8s on the master;
2. copies the Kubernetes manifests to the master;
3. installs local-path-provisioner;
4. creates the movie-app namespace;
5. creates the internal API Secret only if it does not exist;
6. deploys inference and waits for the model to become ready;
7. applies the application and monitoring manifests with the published image tag;
8. waits for application rollouts and then starts the periodic agent.

The old one-CPU VM default cannot host the inference pod, which requests two CPUs.
Use `gcp/terraform/ai.tfvars.example`, which configures a two-CPU master and three
four-CPU workers. This allocates 14 vCPUs in total and stays below the project's
16-vCPU global quota. Pass it explicitly with `-var-file=ai.tfvars.example` when
planning and applying. Changing existing VM types may temporarily stop the VMs;
check the plan before applying.

See [the Kubernetes guide](../k8s/README.md) for startup, persistence limitations,
and the complete AI/agent verification procedure.

Manual scp, storage installation, and kubectl apply commands are not required.

---

# 12. Verify the cluster and application

Check the nodes:

    ansible master -b -m shell -a \
    'KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes'

Expected status:

    master     Ready
    worker1    Ready
    worker2    Ready
    worker3    Ready

Check the application:

    ansible master -b -m shell -a \
    'KUBECONFIG=/etc/kubernetes/admin.conf kubectl get pods,pvc,svc -n movie-app'

Expected results:

- application pods are Running;
- User, Catalog, Recommendations and model-cache PVCs are Bound;
- the frontend exposes NodePort 30080;
- Prometheus and Grafana are running.

---

# 13. Open the application and monitoring

The frontend is available at:

    http://<PUBLIC_NODE_IP>:30080

Prometheus is available at:

    http://<PUBLIC_NODE_IP>:30090

Grafana is available at:

    http://<PUBLIC_NODE_IP>:30030

Grafana is provisioned with Prometheus as its data source and with the project dashboard.

## Troubleshooting deployment

These are the deployment problems encountered while setting up the project on
GCP and their resolutions.

### Inference remains Pending with Insufficient cpu

The default one-CPU workers cannot schedule the inference pod, which requests
two CPUs and has a four-CPU limit. Apply the larger configuration after reviewing
its cost:

    cd /workspace/infra/gcp/terraform
    terraform plan -var-file=ai.tfvars.example
    terraform apply -var-file=ai.tfvars.example

The `master` resource must have
`allow_stopping_for_update = true`, because changing its machine type requires a
temporary stop. After the VM restarts, verify the current IP in GCP and update
`gcphosts` if the public address changed.

This sizing change is required because the inference engine runs the quantised
language model on CPU and accepts four generations in parallel. The original
one-CPU nodes had only one allocatable CPU, so Kubernetes could not schedule the
inference pod. The quota-compatible setup uses a two-CPU master and three
four-CPU workers through `ai.tfvars.example`, for 14 vCPUs total and two vCPUs
of quota headroom. The four-slot server remains enabled, but CPU contention can
increase latency under load. The Inference Pod requests 2 CPUs, is limited to
4 CPUs/3GiB, uses `--threads 4`, `--parallel 4` and `--ctx-size 32768`. The
larger context is required because llama.cpp shares the context across the four
parallel slots. The resulting machine sizes and model settings should be
recorded in the project report so performance measurements are reproducible.

### SSH connection refused after changing VM sizes

Wait for the master to return to `RUNNING`, then update its current external IP
in `gcphosts` and test the inventory:

    ansible targets -m ping

Stopping a VM can change an ephemeral public IP, but does not delete its disks.
Do not repeat the Kubernetes installation playbooks merely because the master
was restarted. If the cluster still exists, run only the application deployment
playbook after SSH and the nodes are healthy.

### kubectl tries localhost:8080

This means the local machine has no Kubernetes context. Run administrative
commands through the master using its kubeconfig:

    ansible master -b -m shell -a \
      'KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes -o wide'

The same approach can be used for pods, services, PVCs, events and logs.

### Deployment rejects the image version

The application deployment requires four published images with an explicit tag.
Pass both variables to Ansible:

    ansible-playbook ansible/ansible-start-deployment.yml \
      -e app_image_prefix=YOUR_DOCKERHUB_USER \
      -e app_image_tag=YOUR_PUBLISHED_TAG

The playbook rejects `latest` and `v1`. The agent reuses the published
Recommendations image, so only four application images need to be built.

### Inference takes a long time on first start

The pinned llama.cpp image downloads and loads the model on its first start.
The first rollout can take roughly 30 minutes. The model cache is stored in the
`model-cache` PVC, so later starts reuse the downloaded model. Inspect the pod
events and logs before interrupting the rollout:

    ansible master -b -m shell -a \
      'KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n movie-app get pods -o wide'

    ansible master -b -m shell -a \
      'KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n movie-app get events --sort-by=.lastTimestamp'

Do not delete the PVC merely to retry the deployment.

---

# 14. Common problems

| Problem | Possible cause |
|---|---|
| ansible targets -m ping fails | Incorrect IP, username, or SSH key |
| kubeadm init fails | Insufficient resources, swap, or incorrect configuration |
| Worker does not join | Join command, port 6443, or network problem |
| ImagePullBackOff | Incorrect image/tag or unpublished image |
| exec format error | Image built for a different architecture; use linux/amd64 |
| PVC remains Pending | local-path-provisioner was not installed |
| CrashLoopBackOff | Inspect the pod logs |
| Running but not Ready | Incorrect probe or port |
| Website does not open | Firewall, NodePort, or IP problem |
| Frontend returns 502/504 | Upstream unavailable, service resolution/connection failure, or timeout; inspect Nginx and backend logs |
| Prometheus has no targets | Incorrect service endpoints or metrics paths |
| Grafana has no data | Prometheus or the dashboard data source is unavailable |

Useful commands:

    ansible master -b -m shell -a \
    'KUBECONFIG=/etc/kubernetes/admin.conf kubectl get pods -n movie-app'

    ansible master -b -m shell -a \
    'KUBECONFIG=/etc/kubernetes/admin.conf kubectl get svc -n movie-app'

    ansible master -b -m shell -a \
    'KUBECONFIG=/etc/kubernetes/admin.conf kubectl get pvc -n movie-app'

For logs:

    ansible master -b -m shell -a \
    'KUBECONFIG=/etc/kubernetes/admin.conf kubectl logs <POD> -n movie-app'

---

# 15. Cleanup is required

At the end of the experiment, destroy the infrastructure so that it does not continue consuming GCP credits.

From the management container:

    cd /workspace/infra/gcp/terraform
    terraform destroy

Confirm with yes.

Then check the Google Cloud Console and confirm that VM instances, disks, project firewall rules, and public IP addresses have been removed.

There is no need to delete the movie-app namespace manually because destroying the VMs removes the Kubernetes cluster.

---

# Quick checklist

Before starting:

- [ ] Docker installed
- [ ] Docker Compose works
- [ ] Docker Hub account available
- [ ] GCP project with billing enabled
- [ ] Compute Engine API enabled
- [ ] Service account created
- [ ] Service account JSON placed in infra/gcp/terraform/
- [ ] JSON excluded from Git
- [ ] SSH key created

Deployment:

- [ ] docker compose up --build works
- [ ] Images published on Docker Hub
- [ ] Docker Hub username is correct in the manifests
- [ ] terraform init
- [ ] terraform plan
- [ ] terraform apply
- [ ] IP addresses updated in gcphosts
- [ ] ansible targets -m ping returns pong
- [ ] ansible-gcp-configure-nodes.yml
- [ ] ansible-k8s-install.yml
- [ ] ansible-create-cluster.yml
- [ ] ansible-workers-join.yml
- [ ] 4 nodes are Ready
- [ ] ansible-start-deployment.yml
- [ ] Pods are Running
- [ ] PVCs are Bound
- [ ] Frontend is accessible on :30080
- [ ] Prometheus is accessible on :30090
- [ ] Grafana is accessible on :30030

At the end:

- [ ] terraform destroy
- [ ] GCP resources confirmed as removed
