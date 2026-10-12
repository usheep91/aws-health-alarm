# CloudWatch 알람은 AWS 관리형 키(alias/aws/sns)로 암호화된 토픽에 게시할 수 없다.
# 암호화가 필요하면 cloudwatch.amazonaws.com에 kms:Decrypt/GenerateDataKey를 허용한 CMK를 지정한다.
resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "notify_dlq" {
  alarm_name          = "${var.name_prefix}-notify-dlq-not-empty"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.notify_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = local.functions
  alarm_name          = "${var.name_prefix}-${each.key}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.fn[each.key].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# event sweep이 3시간 연속 성공하지 못하면 알린다.
resource "aws_cloudwatch_metric_alarm" "reconcile_heartbeat" {
  alarm_name          = "${var.name_prefix}-reconcile-heartbeat"
  namespace           = "AHA"
  metric_name         = "ReconcileSuccess"
  dimensions          = { service = "aha", mode = "event" }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# full resync에서 새 항목이 발견되면 정기 sweep이 놓친 것이다.
resource "aws_cloudwatch_metric_alarm" "reconcile_missed" {
  alarm_name          = "${var.name_prefix}-reconcile-missed-items"
  namespace           = "AHA"
  metric_name         = "ReconcileMissedItems"
  dimensions          = { service = "aha", mode = "full" }
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
