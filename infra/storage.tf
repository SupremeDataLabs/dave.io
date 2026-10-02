resource "aws_s3_bucket" "frontend" {
  bucket        = "${local.name}-web"
  force_destroy = true
}

resource "aws_s3_bucket" "history" {
  bucket        = "${local.name}-history"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "private" {
  for_each = { frontend = aws_s3_bucket.frontend.id, history = aws_s3_bucket.history.id }
  bucket   = each.value

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "encrypted" {
  for_each = { frontend = aws_s3_bucket.frontend.id, history = aws_s3_bucket.history.id }
  bucket   = each.value
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "history" {
  bucket = aws_s3_bucket.history.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.history.arn, "${aws_s3_bucket.history.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_ssm_parameter" "llm_key" {
  count            = var.enable_chat ? 1 : 0
  name             = "/${local.name}/llm-api-key"
  type             = "SecureString"
  value_wo         = var.llm_api_key
  value_wo_version = var.llm_key_version
}
