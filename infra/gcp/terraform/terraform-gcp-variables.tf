# How to define variables in terraform:
# https://www.terraform.io/docs/configuration/variables.html

# ID of the project, find it in the GCP console when clicking 
# on the project name (on the top dropdown)
variable "GCP_PROJECT_ID" {
  default = "psia-projeto"
}

# A list of machine types is found at:
# https://cloud.google.com/compute/docs/machine-types
# prices are defined per region, before choosing an instance
# check the cost at: https://cloud.google.com/compute/pricing#machinetype
# Legacy default; insufficient for inference. See ai.tfvars.example.
variable "GCP_MACHINE_TYPE" {
  default = "n1-standard-1"
}

# Regions list is found at:
# https://cloud.google.com/compute/docs/regions-zones/regions-zones?hl=en_US
# For prices of your deployment check:
# Compute Engine dashboard -> VM instances -> Zone
variable "GCP_ZONE" {
  default = "europe-west3-c"
}

# Optional override: size worker2 (count.index == 1) for inference.
# null preserves the existing deployment sizes unless explicitly configured.
variable "GCP_WORKER_MACHINE_TYPE" {
  type        = string
  default     = null
  description = "Machine type override for worker2 only (count.index == 1); defaults to GCP_MACHINE_TYPE when unset."
}
