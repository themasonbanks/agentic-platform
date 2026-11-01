terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.71"
    }
  }
}
variable "bucket_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "Supply a unique reviewed bucket name."
  }
}
variable "free_plan_eligibility_confirmed" {
  type    = bool
  default = false
  validation {
    condition     = var.free_plan_eligibility_confirmed
    error_message = "Verify account eligibility, dedicated WAF and FREE-plan feature limits before planning hosting."
  }
}
variable "web_acl_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:wafv2:us-east-1:[0-9]{12}:global/webacl/.+$", var.web_acl_arn))
    error_message = "Supply a reviewed CloudFront-scope WAF ARN."
  }
}
variable "content_kms_key_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:kms:eu-west-2:[0-9]{12}:key/.+$", var.content_kms_key_arn))
    error_message = "Supply a reviewed London content key, separate from state encryption."
  }
}
resource "aws_s3_bucket" "site" {
  bucket        = var.bucket_name
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.content_kms_key_arn
    }
  }
}
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = var.bucket_name
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}
resource "aws_cloudfront_cache_policy" "site" {
  name        = "${var.bucket_name}-static"
  min_ttl     = 0
  default_ttl = 60
  max_ttl     = 300
  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true
    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}
resource "aws_cloudfront_distribution" "site" {
  web_acl_id          = var.web_acl_arn
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "private-site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }
  default_cache_behavior {
    target_origin_id       = "private-site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = aws_cloudfront_cache_policy.site.id
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate { cloudfront_default_certificate = true }
}
resource "aws_pricingplanmanager_subscription" "site" {
  plan_family   = "CloudFront"
  plan_tier     = "FREE"
  usage_level   = "DEFAULT"
  resource_arns = [aws_cloudfront_distribution.site.arn, var.web_acl_arn]
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow", Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject", Resource = "${aws_s3_bucket.site.arn}/*"
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn } }
      },
      {
        Effect    = "Deny", Principal = "*", Action = "s3:*"
        Resource  = [aws_s3_bucket.site.arn, "${aws_s3_bucket.site.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }
    ]
  })
}
output "preview_url" { value = "https://${aws_cloudfront_distribution.site.domain_name}" }
output "bucket_name" { value = aws_s3_bucket.site.id }
output "distribution_id" { value = aws_cloudfront_distribution.site.id }

output "distribution_arn" { value = aws_cloudfront_distribution.site.arn }
output "pricing_plan_arn" { value = aws_pricingplanmanager_subscription.site.arn }
output "pricing_plan_status" { value = aws_pricingplanmanager_subscription.site.status }
