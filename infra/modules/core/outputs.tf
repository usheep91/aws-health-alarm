output "interaction_url" {
  description = "Slack App Interactivity Request URL"
  value       = "${aws_apigatewayv2_api.slack.api_endpoint}/slack/interactions"
}

output "notify_queue_url" {
  value = aws_sqs_queue.notify.url
}

output "table_name" {
  value = aws_dynamodb_table.state.name
}
