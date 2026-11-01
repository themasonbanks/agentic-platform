mock_provider "aws" {
  mock_resource "aws_s3_bucket" {
    defaults = { id = "site-fixture", arn = "arn:aws:s3:::site-fixture", bucket_regional_domain_name = "site-fixture.s3.eu-west-2.amazonaws.com" }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = { arn = "arn:aws:cloudfront::123456789012:distribution/FIXTURE", domain_name = "fixture.cloudfront.net" }
  }
}
variables { bucket_name = "site-fixture" }
run "private_static_origin" {
  command = apply
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
