variable "name_prefix" {
  description = "리소스 이름 접두사 (예: aha-dev)"
  type        = string
}

variable "lambda_zip_path" {
  description = "scripts/build_lambda.sh 산출물 경로"
  type        = string
}

variable "config_yaml" {
  description = "SSM에 저장할 애플리케이션 설정 YAML"
  type        = string
}

variable "slack_secret_arn" {
  description = "bot_token, signing_secret을 담은 Secrets Manager 시크릿 ARN (Terraform 외부에서 생성)"
  type        = string
}

variable "alarm_email" {
  description = "운영 알람 수신 이메일 (Slack과 별도 경로)"
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "tags" {
  type    = map(string)
  default = {}
}
