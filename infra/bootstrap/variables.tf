variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "ask-dave"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.project_name))
    error_message = "Use 3-20 lowercase letters, digits or hyphens; start with a letter."
  }
}

variable "identity_center_region" {
  description = "Home region of the IAM Identity Center instance used to assume the deployment role."
  type        = string
  default     = "us-east-1"
}

variable "identity_center_permission_set_name" {
  description = "Permission set whose generated AWSReservedSSO role may assume the deployment role."
  type        = string
  default     = "ask-dave-TerraformAccess"
}
