
# Elemets of the cloud such as virtual servers,
# networks, firewall rules are created as resources
# syntax is: resource RESOURCE_TYPE RESOURCE_NAME
# https://www.terraform.io/docs/configuration/resources.html

###########  Master  #############
resource "google_compute_instance" "master" {
  name         = "master"
  machine_type = var.GCP_MACHINE_TYPE
  zone         = var.GCP_ZONE
  
  allow_stopping_for_update = true
  
  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
    }
  }

  network_interface {
    network = "default"
    access_config {
    }
  }

  metadata = {
    ssh-keys = "ubuntu:${file("/root/.ssh/id_rsa.pub")}"
  }

  tags = ["k8s", "master"]
}

###########  Workers  #############
resource "google_compute_instance" "worker" {
  count                     = 3
  name                      = "worker${count.index + 1}"
  machine_type              = coalesce(var.GCP_WORKER_MACHINE_TYPE, var.GCP_MACHINE_TYPE)
  allow_stopping_for_update = true
  zone                      = var.GCP_ZONE

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
    }
  }

  network_interface {
    network = "default"
    access_config {
    }
  }

  metadata = {
    ssh-keys = "ubuntu:${file("/root/.ssh/id_rsa.pub")}"
  }

  tags = ["k8s", "worker"]
}
