mock_provider "aws" {
  mock_resource "aws_pricingplanmanager_subscription" {
    defaults = { status = "ACTIVE" }
  }
  mock_resource "aws_kms_key" {
    defaults = { id = "fixture", arn = "arn:aws:kms:eu-west-2:123456789012:key/fixture" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { id = "development-fixture", arn = "arn:aws:s3:::development-fixture", bucket_regional_domain_name = "development-fixture.s3.eu-west-2.amazonaws.com" }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = { arn = "arn:aws:cloudfront::123456789012:distribution/FIXTURE", domain_name = "fixture.cloudfront.net" }
  }
  mock_data "aws_cloudfront_cache_policy" {
    defaults = { id = "658327ea-f89d-4fab-a63d-7e88639e58f6" }
  }
}
mock_provider "aws" {
  alias = "global"
  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:us-east-1:123456789012:global/webacl/fixture/unit" }
  }
}
variables {
  aws_account_id                  = "123456789012"
  bucket_name                     = "production-fixture"
  free_plan_eligibility_confirmed = true
  global_waf_region_approved      = true
  production_release_approved     = true
}
run "environment_composition" {
  command = apply
  assert {
    condition     = output.environment == "production" && output.preview_url == "https://fixture.cloudfront.net"
    error_message = "The root must compose the correct environment and preview output."
  }
}
run "reject_unapproved_global_region" {
  command = plan
  variables { global_waf_region_approved = false }
  expect_failures = [var.global_waf_region_approved]
}
run "reject_unapproved_production_release" {
  command = plan
  variables { production_release_approved = false }
  expect_failures = [var.production_release_approved]
}
