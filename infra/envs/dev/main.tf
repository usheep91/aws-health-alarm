terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # 원격 상태는 조직 표준 백엔드를 사용한다. 예:
  # backend "s3" {}
}

provider "aws" {
  region = "ap-northeast-2"
}

module "core" {
  source           = "../../modules/core"
  name_prefix      = "aha-dev"
  lambda_zip_path  = "${path.root}/../../../build/lambda.zip"
  config_yaml      = file("${path.module}/config.yaml")
  slack_secret_arn = var.slack_secret_arn
  alarm_email      = var.alarm_email
  tags = {
    Project     = "aws-org-health-aware"
    Environment = "dev"
  }
}
