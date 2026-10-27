
# Elemets of the cloud such as virtual servers,
# networks, firewall rules are created as resources
# syntax is: resource RESOURCE_TYPE RESOURCE_NAME
# https://www.terraform.io/docs/configuration/resources.html

# Acesso ao site (NodePort) e à API do Kubernetes a partir do exterior
resource "google_compute_firewall" "k8s_external" {
  name    = "k8s-external"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "6443", "30000-32767"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["k8s"]
}

# Tráfego entre os nós do cluster
resource "google_compute_firewall" "k8s_internal" {
  name    = "k8s-internal"
  network = "default"

  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
  }
  allow {
    protocol = "icmp"
  }
  allow {
    protocol = "ipip" # usado pelo Calico por omissão
  }

  source_tags = ["k8s"]
  target_tags = ["k8s"]
}