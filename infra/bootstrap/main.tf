locals {
  lambda_role_prefix           = "${var.project_name}-"
  lambda_boundary_arn          = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/${var.project_name}-LambdaBoundary"
  stack_resource_prefix        = "${var.project_name}-"
  identity_center_role_path    = var.identity_center_region == "us-east-1" ? "aws-reserved/sso.amazonaws.com" : "aws-reserved/sso.amazonaws.com/${var.identity_center_region}"
  identity_center_role_pattern = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.identity_center_role_path}/AWSReservedSSO_${var.identity_center_permission_set_name}_*"
}

# The boundary caps every Lambda execution role the deploy role can create or
# update. The deploy role is deliberately not allowed to edit this policy.
resource "aws_iam_policy" "lambda_boundary" {
  name        = "${var.project_name}-LambdaBoundary"
  description = "Maximum permissions for Dave.io Lambda execution roles."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteOnlyToDaveIoLambdaLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-*:*"
      },
      {
        Sid      = "ListDaveIoHistoryPrefix"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.project_name}-*"
        Condition = {
          StringLike = { "s3:prefix" = ["chats/*"] }
        }
      },
      {
        Sid      = "ReadAndWriteDaveIoChatObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.project_name}-*/chats/*"
      },
      {
        Sid      = "ReadDaveIoLlmParameter"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/${var.project_name}-*/llm-api-key"
      }
    ]
  })
}

resource "aws_iam_role" "terraform_deploy" {
  name                 = "${var.project_name}-TerraformDeployRole"
  description          = "Temporary, stack-scoped permissions for Dave.io Terraform deployments."
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root" }
      Condition = {
        ArnLike = { "aws:PrincipalArn" = local.identity_center_role_pattern }
      }
    }]
  })
  tags = { Purpose = "Terraform deployment" }
}

resource "aws_iam_role_policy" "terraform_deploy" {
  name = "${var.project_name}-StackProvisioning"
  role = aws_iam_role.terraform_deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CreateDaveIoBuckets"
        Effect   = "Allow"
        Action   = ["s3:CreateBucket"]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${local.stack_resource_prefix}*"
      },
      {
        Sid    = "ManageDaveIoBucketConfiguration"
        Effect = "Allow"
        Action = [
          "s3:DeleteBucket", "s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketVersions",
          "s3:GetBucketAcl", "s3:GetBucketCORS", "s3:GetBucketWebsite", "s3:GetBucketLogging",
          "s3:GetAccelerateConfiguration", "s3:GetBucketOwnershipControls",
          "s3:GetBucketRequestPayment", "s3:GetReplicationConfiguration", "s3:GetBucketObjectLockConfiguration",
          "s3:GetBucketPolicy", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy",
          "s3:GetEncryptionConfiguration", "s3:PutEncryptionConfiguration",
          "s3:GetBucketPublicAccessBlock", "s3:PutBucketPublicAccessBlock",
          "s3:GetBucketTagging", "s3:PutBucketTagging",
          "s3:GetBucketVersioning", "s3:PutBucketVersioning", "s3:GetLifecycleConfiguration",
          "s3:PutLifecycleConfiguration"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${local.stack_resource_prefix}*"
      },
      {
        Sid    = "ManageDaveIoBucketObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:GetObjectVersion",
          "s3:GetObjectTagging", "s3:PutObjectTagging", "s3:DeleteObjectTagging"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${local.stack_resource_prefix}*/*"
      },
      {
        Sid      = "ListBucketsForTerraformDiscovery"
        Effect   = "Allow"
        Action   = ["s3:ListAllMyBuckets"]
        Resource = "*"
      },
      {
        Sid    = "ManageDaveIoLambdaFunctions"
        Effect = "Allow"
        Action = [
          "lambda:CreateFunction", "lambda:DeleteFunction", "lambda:GetFunction",
          "lambda:GetFunctionConfiguration", "lambda:GetFunctionCodeSigningConfig",
          "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration",
          "lambda:AddPermission", "lambda:RemovePermission", "lambda:GetPolicy",
          "lambda:TagResource", "lambda:UntagResource", "lambda:ListTags", "lambda:ListVersionsByFunction"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${local.stack_resource_prefix}*"
      },
      {
        Sid      = "ListLambdaFunctions"
        Effect   = "Allow"
        Action   = ["lambda:ListFunctions"]
        Resource = "*"
      },
      {
        Sid    = "ManageDaveIoHttpApis"
        Effect = "Allow"
        Action = [
          "apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:PATCH", "apigateway:DELETE",
          "apigateway:TagResource", "apigateway:UntagResource"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:apigateway:${var.aws_region}::/*"
      },
      {
        Sid    = "ManageDaveIoCloudFrontResources"
        Effect = "Allow"
        Action = [
          "cloudfront:CreateDistribution", "cloudfront:GetDistribution", "cloudfront:UpdateDistribution",
          "cloudfront:DeleteDistribution", "cloudfront:ListDistributions", "cloudfront:CreateOriginAccessControl",
          "cloudfront:GetOriginAccessControl", "cloudfront:UpdateOriginAccessControl",
          "cloudfront:DeleteOriginAccessControl", "cloudfront:ListOriginAccessControls",
          "cloudfront:CreateCachePolicy", "cloudfront:GetCachePolicy", "cloudfront:DeleteCachePolicy",
          "cloudfront:ListCachePolicies", "cloudfront:TagResource", "cloudfront:UntagResource",
          "cloudfront:ListTagsForResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "ManageDaveIoLogGroups"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:DescribeLogGroups",
          "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy", "logs:ListTagsForResource",
          "logs:TagResource", "logs:UntagResource"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/*/${local.stack_resource_prefix}*"
      },
      {
        Sid      = "DescribeLogGroupsForTerraform"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "*"
      },
      {
        Sid      = "ManageDaveIoAlarms"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:DescribeAlarms", "cloudwatch:ListTagsForResource", "cloudwatch:TagResource", "cloudwatch:UntagResource"]
        Resource = "arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.stack_resource_prefix}*"
      },
      {
        Sid    = "ManageDaveIoLlmParameter"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter", "ssm:GetParameter", "ssm:DeleteParameter",
          "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource", "ssm:ListTagsForResource"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/${local.stack_resource_prefix}*"
      },
      {
        # DescribeParameters does not support parameter-level resource scoping.
        Sid      = "DescribeParameterMetadataForTerraform"
        Effect   = "Allow"
        Action   = ["ssm:DescribeParameters"]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:RequestedRegion" = var.aws_region }
        }
      },
      {
        Sid    = "ManageDaveIoLambdaRolesWithinBoundary"
        Effect = "Allow"
        Action = [
          "iam:DeleteRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy",
          "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:TagRole",
          "iam:UntagRole", "iam:ListRoleTags"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.stack_resource_prefix}*-LambdaExecution"
      },
      {
        Sid      = "CreateDaveIoLambdaRolesWithBoundary"
        Effect   = "Allow"
        Action   = ["iam:CreateRole"]
        Resource = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.stack_resource_prefix}*"
        Condition = {
          StringEquals = { "iam:PermissionsBoundary" = local.lambda_boundary_arn }
        }
      },
      {
        Sid      = "KeepDaveIoLambdaRoleBoundaryAttached"
        Effect   = "Allow"
        Action   = ["iam:PutRolePermissionsBoundary"]
        Resource = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.stack_resource_prefix}*"
        Condition = {
          StringEquals = { "iam:PermissionsBoundary" = local.lambda_boundary_arn }
        }
      },
      {
        Sid      = "ReadBoundaryPolicy"
        Effect   = "Allow"
        Action   = ["iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicies"]
        Resource = "*"
      },
      {
        Sid      = "PassOnlyDaveIoLambdaRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.stack_resource_prefix}*"
        Condition = {
          StringEquals = { "iam:PassedToService" = "lambda.amazonaws.com" }
        }
      },
      {
        Sid      = "AllowCloudFrontServiceLinkedRoleCreation"
        Effect   = "Allow"
        Action   = ["iam:CreateServiceLinkedRole"]
        Resource = "*"
        Condition = {
          StringEquals = { "iam:AWSServiceName" = "cloudfront.amazonaws.com" }
        }
      }
    ]
  })
}
