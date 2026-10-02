resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 7
}

resource "aws_iam_role" "lambda" {
  name                 = "${local.name}-LambdaExecution"
  permissions_boundary = data.aws_iam_policy.lambda_boundary.arn
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

data "aws_iam_policy" "lambda_boundary" {
  name = "${var.project_name}-LambdaBoundary"
}

resource "aws_iam_role_policy" "lambda" {
  name = "required-resources-only"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.lambda.arn}:*"
      },
      {
        Effect    = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.history.arn
        Condition = { StringLike = { "s3:prefix" = ["chats/*"] } }
      },
      {
        Effect   = "Allow", Action = var.enable_chat ? ["s3:GetObject", "s3:PutObject"] : ["s3:GetObject"]
        Resource = "${aws_s3_bucket.history.arn}/chats/*"
      }
      ], var.enable_chat ? [{
        Effect = "Allow", Action = ["ssm:GetParameter"], Resource = aws_ssm_parameter.llm_key[0].arn
    }] : [])
  })
}

resource "aws_lambda_function" "app" {
  function_name    = local.name
  role             = aws_iam_role.lambda.arn
  handler          = "app.handler"
  runtime          = "python3.13"
  architectures    = ["arm64"]
  filename         = "${path.module}/../.build/lambda.zip"
  source_code_hash = filebase64sha256("${path.module}/../.build/lambda.zip")
  memory_size      = 256
  timeout          = 28

  environment {
    variables = {
      HISTORY_BUCKET = aws_s3_bucket.history.id
      LLM_PARAMETER  = var.enable_chat ? aws_ssm_parameter.llm_key[0].name : ""
      LLM_MODEL      = var.llm_model
    }
  }
  depends_on = [aws_iam_role_policy.lambda, aws_cloudwatch_log_group.lambda]
}

resource "aws_apigatewayv2_api" "app" {
  name          = local.name
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["https://${aws_cloudfront_distribution.frontend.domain_name}"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "app" {
  api_id                 = aws_apigatewayv2_api.app.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.app.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "app" {
  for_each  = toset(["GET /history", "POST /chat"])
  api_id    = aws_apigatewayv2_api.app.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.app.id}"
}

resource "aws_apigatewayv2_stage" "app" {
  api_id      = aws_apigatewayv2_api.app.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app.execution_arn}/*/*"
}
