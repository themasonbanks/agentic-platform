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
  bucket_name                     = "development-fixture"
  free_plan_eligibility_confirmed = true
  global_waf_region_approved      = true
}
run "composed_development_controls" {
  command = apply
  assert {
    condition     = aws_kms_key.content.enable_key_rotation && aws_kms_key.content.deletion_window_in_days == 30
    error_message = "Content key needs rotation and a reviewed recovery window."
  }
  assert {
    condition     = jsondecode(aws_kms_key_policy.content.policy).Statement[1].Condition.StringEquals["AWS:SourceArn"] == "arn:aws:cloudfront::123456789012:distribution/FIXTURE" && jsondecode(aws_kms_key_policy.content.policy).Statement[1].Action == "kms:Decrypt"
    error_message = "CloudFront may only decrypt for this distribution."
  }
  assert {
    condition     = aws_wafv2_web_acl.site.scope == "CLOUDFRONT" && one(aws_wafv2_web_acl.site.rule).statement[0].rate_based_statement[0].limit == 2000 && !one(aws_wafv2_web_acl.site.visibility_config).sampled_requests_enabled
    error_message = "Use dedicated global rate protection without sampled request payloads."
  }
}
run "reject_unapproved_global_region" {
  command = plan
  variables { global_waf_region_approved = false }
  expect_failures = [var.global_waf_region_approved]
}
