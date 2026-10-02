terraform {
  required_version = ">= 1.11, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = { Project = "Dave.io", Stack = local.name, ManagedBy = "Terraform" }
  }
}

resource "random_id" "deployment" {
  byte_length = 6
}

locals {
  name = "${var.project_name}-${random_id.deployment.hex}"
}
