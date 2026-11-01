mock_provider "aws" {
  mock_resource "aws_s3_bucket" {
    defaults = { id = "site-fixture", arn = "arn:aws:s3:::site-fixture", bucket_regional_domain_name = "site-fixture.s3.eu-west-2.amazonaws.com" }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = { arn = "arn:aws:cloudfront::123456789012:distribution/FIXTURE", domain_name = "fixture.cloudfront.net" }
  }
}
variables {
  bucket_name         = "site-fixture"
  web_acl_arn         = "arn:aws:wafv2:us-east-1:123456789012:global/webacl/fixture/unit"
  content_kms_key_arn = "arn:aws:kms:eu-west-2:123456789012:key/fixture"
}
run "private_static_origin" {
  command = apply
  assert {
    condition     = aws_cloudfront_distribution.site.web_acl_id == var.web_acl_arn && one(one(aws_s3_bucket_server_side_encryption_configuration.site.rule).apply_server_side_encryption_by_default).kms_master_key_id == var.content_kms_key_arn
    error_message = "Explicit reviewed WAF and content encryption are required."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.site.block_public_acls && aws_s3_bucket_public_access_block.site.block_public_policy && aws_s3_bucket_public_access_block.site.ignore_public_acls && aws_s3_bucket_public_access_block.site.restrict_public_buckets
    error_message = "Origin must block public bucket access."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.site.policy).Statement[0].Condition.StringEquals["AWS:SourceArn"] == aws_cloudfront_distribution.site.arn && aws_cloudfront_origin_access_control.site.signing_behavior == "always"
    error_message = "Only this signed distribution may read the origin."
  }
  assert {
    condition     = !aws_s3_bucket.site.force_destroy && aws_s3_bucket_versioning.site.versioning_configuration[0].status == "Enabled"
    error_message = "Keep recoverable versions and require explicit object cleanup."
  }
  assert {
    condition     = aws_cloudfront_distribution.site.default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https"
    error_message = "Viewer traffic must use HTTPS."
  }
}

run "reject_regional_waf" {
  command = plan
  variables {
    web_acl_arn = "arn:aws:wafv2:eu-west-2:123456789012:regional/webacl/fixture/unit"
  }
  expect_failures = [var.web_acl_arn]
}
run "reject_non_london_key" {
  command = plan
  variables {
    content_kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/fixture"
  }
  expect_failures = [var.content_kms_key_arn]
}
