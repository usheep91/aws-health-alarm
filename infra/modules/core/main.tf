locals {
  functions = {
    reconcile = {
      handler = "aha.handlers.reconcile.lambda_handler"
      timeout = 900
      memory  = 512
    }
    notifier = {
      handler = "aha.handlers.notifier.lambda_handler"
      timeout = 120
      memory  = 256
    }
    interaction = {
      handler = "aha.handlers.interaction.lambda_handler"
      timeout = 10
      memory  = 256
    }
    reminder = {
      handler = "aha.handlers.reminder.lambda_handler"
      timeout = 300
      memory  = 256
    }
  }

  common_env = {
    TABLE_NAME                   = aws_dynamodb_table.state.name
    NOTIFY_QUEUE_URL             = aws_sqs_queue.notify.url
    CONFIG_PARAM                 = aws_ssm_parameter.config.name
    SLACK_SECRET_ARN             = var.slack_secret_arn
    POWERTOOLS_SERVICE_NAME      = "aha"
    POWERTOOLS_METRICS_NAMESPACE = "AHA"
    # 설정은 콜드 스타트 때만 SSM에서 읽으므로, 변경 시 환경변수를 바꿔 Lambda를 새 버전으로 롤링한다.
    CONFIG_SHA = sha256(var.config_yaml)
  }
}

resource "aws_dynamodb_table" "state" {
  name                        = "${var.name_prefix}-state"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "gsi1pk"
    type = "S"
  }
  attribute {
    name = "gsi1sk"
    type = "S"
  }

  global_secondary_index {
    name            = "gsi-state"
    hash_key        = "gsi1pk"
    range_key       = "gsi1sk"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags
}

resource "aws_sqs_queue" "notify_dlq" {
  name                      = "${var.name_prefix}-notify-dlq.fifo"
  fifo_queue                = true
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

# visibility timeout은 notifier timeout(120초)의 6배.
resource "aws_sqs_queue" "notify" {
  name                       = "${var.name_prefix}-notify.fifo"
  fifo_queue                 = true
  visibility_timeout_seconds = 720
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.notify_dlq.arn
    maxReceiveCount     = 5
  })
  tags = var.tags
}

resource "aws_ssm_parameter" "config" {
  name  = "/${var.name_prefix}/config"
  type  = "String"
  value = var.config_yaml
  tags  = var.tags
}
