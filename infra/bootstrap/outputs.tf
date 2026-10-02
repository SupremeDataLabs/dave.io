output "deployment_role_arn" {
  value       = aws_iam_role.terraform_deploy.arn
  description = "Assume this role for the Dave.io application Terraform stack."
}

output "lambda_permissions_boundary_arn" {
  value       = aws_iam_policy.lambda_boundary.arn
  description = "Maximum permissions attached to Dave.io Lambda execution roles."
}
