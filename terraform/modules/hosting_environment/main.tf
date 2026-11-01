terraform {
  required_version = ">= 1.17.1, < 2.0"
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      version               = "~> 6.71"
      configuration_aliases = [aws.global]
    }
  }
}
variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Supply the reviewed AWS account ID."
  }
}
variable "bucket_name" { type = string }
variable "free_plan_eligibility_confirmed" { type = bool }
variable "environment" {
  type = string
  validation {
    condition     = contains(["development", "production"], var.environment)
    error_message = "Use a separate development or production state root."
  }
}
resource "aws_wafv2_web_acl" "site" {
  provider = aws.global
  name     = "${var.bucket_name}-${var.environment}"
  scope    = "CLOUDFRONT"
  default_action {
    allow {}
  }
  rule {
    name     = "per-ip-rate-limit"
    priority = 0
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit                 = 2000
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      sampled_requests_enabled   = false
      metric_name                = "${var.environment}-ip-rate-limit"
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    sampled_requests_enabled   = false
    metric_name                = "${var.environment}-static-site"
  }
}
resource "aws_kms_key" "content" {
  description             = "${title(var.environment)} static content encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  lifecycle { prevent_destroy = true }
}
resource "aws_kms_key_policy" "content" {
  key_id = aws_kms_key.content.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdministration", Effect = "Allow"
        Principal = { AWS = "arn:aws:iam::${var.aws_account_id}:root" }
        Action    = "kms:*", Resource = "*"
      },
      {
        Sid       = "DistributionReadOnly", Effect = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "kms:Decrypt", Resource = "*"
        Condition = { StringEquals = { "AWS:SourceArn" = module.site.distribution_arn } }
      }
    ]
  })
}
module "site" {
  source                          = "../static_site"
  bucket_name                     = var.bucket_name
  free_plan_eligibility_confirmed = var.free_plan_eligibility_confirmed
  web_acl_arn                     = aws_wafv2_web_acl.site.arn
  content_kms_key_arn             = aws_kms_key.content.arn
}
output "preview_url" {
  value      = module.site.preview_url
  depends_on = [aws_kms_key_policy.content]
}
output "pricing_plan_status" { value = module.site.pricing_plan_status }
output "pricing_plan_arn" { value = module.site.pricing_plan_arn }

output "environment" { value = var.environment }
