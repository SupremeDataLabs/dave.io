terraform {
  required_version = ">= 1.11, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = { Project = "Dave.io", ManagedBy = "Terraform", Purpose = "Deployment access" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
