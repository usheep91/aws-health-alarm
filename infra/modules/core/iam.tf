data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "common" {
  statement {
    sid = "StateTable"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:BatchWriteItem",
    ]
    resources = [aws_dynamodb_table.state.arn, "${aws_dynamodb_table.state.arn}/index/*"]
  }
  statement {
    sid       = "Config"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.config.arn]
  }
}

data "aws_iam_policy_document" "reconcile" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]
  statement {
    sid = "HealthOrganizationRead"
    actions = [
      "health:DescribeEventsForOrganization",
      "health:DescribeEventDetailsForOrganization",
      "organizations:ListAccounts",
      "health:DescribeAffectedAccountsForOrganization",
      "health:DescribeAffectedEntitiesForOrganization",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "PublishIntents"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.notify.arn]
  }
}

data "aws_iam_policy_document" "notifier" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]
  statement {
    sid = "ConsumeIntents"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.notify.arn]
  }
  statement {
    sid       = "SlackSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.slack_secret_arn]
  }
}

data "aws_iam_policy_document" "interaction" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]
  statement {
    sid       = "SlackSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.slack_secret_arn]
  }
  statement {
    sid       = "PublishIntents"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.notify.arn]
  }
}

locals {
  function_policies = {
    reconcile   = data.aws_iam_policy_document.reconcile.json
    notifier    = data.aws_iam_policy_document.notifier.json
    interaction = data.aws_iam_policy_document.interaction.json
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.functions
  name               = "${var.name_prefix}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "basic" {
  for_each   = local.functions
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.functions
  name     = "${var.name_prefix}-${each.key}"
  role     = aws_iam_role.fn[each.key].id
  policy   = local.function_policies[each.key]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.fn["reconcile"].arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${var.name_prefix}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}
