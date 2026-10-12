resource "aws_apigatewayv2_api" "slack" {
  name          = "${var.name_prefix}-slack"
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_integration" "interaction" {
  api_id                 = aws_apigatewayv2_api.slack.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn["interaction"].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "interaction" {
  api_id    = aws_apigatewayv2_api.slack.id
  route_key = "POST /slack/interactions"
  target    = "integrations/${aws_apigatewayv2_integration.interaction.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.slack.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  tags = var.tags
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowSlackApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["interaction"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.slack.execution_arn}/*/*/slack/interactions"
}
