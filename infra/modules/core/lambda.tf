locals {
  reconcile_schedules = {
    event  = "rate(1 hour)"
    entity = "rate(6 hours)"
    full   = "cron(0 3 ? * SUN *)"
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${var.name_prefix}-${each.key}"
  role             = aws_iam_role.fn[each.key].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = each.value.handler
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  timeout          = each.value.timeout
  memory_size      = each.value.memory
  # sweep 동시 실행으로 Health API 스로틀링이 나지 않도록 reconcile은 1개로 제한한다.
  reserved_concurrent_executions = each.key == "reconcile" ? 1 : -1

  environment {
    variables = local.common_env
  }

  depends_on = [aws_cloudwatch_log_group.fn]
  tags       = var.tags
}

resource "aws_lambda_event_source_mapping" "notify" {
  event_source_arn        = aws_sqs_queue.notify.arn
  function_name           = aws_lambda_function.fn["notifier"].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

resource "aws_scheduler_schedule" "reconcile" {
  for_each                     = local.reconcile_schedules
  name                         = "${var.name_prefix}-reconcile-${each.key}"
  schedule_expression          = each.value
  schedule_expression_timezone = "Asia/Seoul"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.fn["reconcile"].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ mode = each.key })

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}
