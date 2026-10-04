variable "enable_delivery" {
  description = "Opt in after configuring GitHub production approval and reviewing state migration."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "Exact owner/repository allowed to use OIDC."
  type        = string
  default     = "SupremeDataLabs/dave.io"
}

variable "existing_github_oidc_provider_arn" {
  description = "Reuse an existing GitHub OIDC provider in this account, if present."
  type        = string
  default     = ""
}

locals {
  delivery_roles  = var.enable_delivery ? toset(["plan", "deploy"]) : toset([])
  delivery_policy = jsondecode(aws_iam_role_policy.terraform_deploy.policy)
  read_statements = [for s in local.delivery_policy.Statement : merge(s, {
    Action = [for a in s.Action : a if can(regex(":(Get|List|Describe|GET)", a))]
  }) if length([for a in s.Action : a if can(regex(":(Get|List|Describe|GET)", a))]) > 0]
  github_provider_arn = var.existing_github_oidc_provider_arn != "" ? var.existing_github_oidc_provider_arn : try(aws_iam_openid_connect_provider.github[0].arn, "")
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.enable_delivery && var.existing_github_oidc_provider_arn == "" ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_s3_bucket" "delivery" {
  count = var.enable_delivery ? 1 : 0
  # Outside the application's ask-dave-* provisioning scope.
  bucket        = "daveio-tf-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = false
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_public_access_block" "delivery" {
  count                   = var.enable_delivery ? 1 : 0
  bucket                  = aws_s3_bucket.delivery[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "delivery" {
  count  = var.enable_delivery ? 1 : 0
  bucket = aws_s3_bucket.delivery[0].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "delivery" {
  count  = var.enable_delivery ? 1 : 0
  bucket = aws_s3_bucket.delivery[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "delivery" {
  count  = var.enable_delivery ? 1 : 0
  bucket = aws_s3_bucket.delivery[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.delivery[0].arn, "${aws_s3_bucket.delivery[0].arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "delivery_plans" {
  count  = var.enable_delivery ? 1 : 0
  bucket = aws_s3_bucket.delivery[0].id
  rule {
    id     = "ExpirePlansNotState"
    status = "Enabled"
    filter { prefix = "plans/" }
    expiration { days = 3 }
    noncurrent_version_expiration { noncurrent_days = 3 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  depends_on = [aws_s3_bucket_versioning.delivery]
}

resource "aws_iam_role" "github" {
  for_each = local.delivery_roles
  name     = "${var.project_name}-GitHub-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_provider_arn }
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = each.key == "plan" ? "repo:${var.github_repository}:ref:refs/heads/main" : "repo:${var.github_repository}:environment:production"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "github_stack" {
  for_each = local.delivery_roles
  name     = "ApplicationStack"
  role     = aws_iam_role.github[each.key].id
  policy = each.key == "plan" ? jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.read_statements, [{
      Sid      = "NoAutomatedSecretMutation", Effect = "Deny"
      Action   = ["ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters"]
      Resource = "*"
    }])
    }) : jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.delivery_policy.Statement, [{
      Sid      = "NoAutomatedSecretMutation", Effect = "Deny"
      Action   = ["ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters"]
      Resource = "*"
    }])
  })
}

resource "aws_iam_role_policy" "github_state" {
  for_each = local.delivery_roles
  name     = "PrivateStateAndPlans"
  role     = aws_iam_role.github[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.delivery[0].arn },
      { Effect = "Allow", Action = each.key == "plan" ? ["s3:GetObject"] : ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.delivery[0].arn}/application/terraform.tfstate" },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.delivery[0].arn}/application/terraform.tfstate.tflock" },
      { Effect = "Allow", Action = each.key == "plan" ? ["s3:GetObject", "s3:PutObject"] : ["s3:GetObject"], Resource = "${aws_s3_bucket.delivery[0].arn}/plans/*" }
    ]
  })
}

resource "aws_iam_role_policy" "human_state" {
  count = var.enable_delivery ? 1 : 0
  name  = "ApplicationRemoteState"
  role  = aws_iam_role.terraform_deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.delivery[0].arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.delivery[0].arn}/application/terraform.tfstate" },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.delivery[0].arn}/application/terraform.tfstate.tflock" }
    ]
  })
}

output "delivery_state_bucket" {
  value = try(aws_s3_bucket.delivery[0].id, null)
}
output "github_role_arns" {
  value = { for k, r in aws_iam_role.github : k => r.arn }
}
