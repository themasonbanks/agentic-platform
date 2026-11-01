mock_provider "aws" {
  mock_resource "aws_s3_bucket" {
    defaults = { id = "agentic-state-unit", arn = "arn:aws:s3:::agentic-state-unit" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:eu-west-2:123456789012:key/unit-test" }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
}

variables {
  aws_account_id             = "123456789012"
  state_bucket_name          = "agentic-state-unit"
  github_oidc_subject_prefix = "repo:themasonbanks@79837282/stockpot@1319669903"
}

run "isolated_state_and_roles" {
  command = apply
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.state.policy).Statement[3].Condition.StringEquals["s3:x-amz-server-side-encryption"] == "aws:kms" && jsondecode(aws_s3_bucket_policy.state.policy).Statement[3].Condition.Null["s3:x-amz-server-side-encryption-aws-kms-key-id"] == "true"
    error_message = "An explicit KMS request without the reviewed key must be denied."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.state.policy).Statement[1].Condition.Null["s3:x-amz-server-side-encryption"] == "false" && jsondecode(aws_s3_bucket_policy.state.policy).Statement[2].Condition.StringNotEquals["s3:x-amz-server-side-encryption-aws-kms-key-id"] == aws_kms_key.state.arn
    error_message = "Explicit encryption overrides must be denied without denying omitted headers."
  }
  assert {
    condition     = contains(jsondecode(aws_iam_role_policy.state["development_apply"].policy).Statement[2].Resource, "arn:aws:s3:::agentic-state-unit/stockpot/development/workspaces/*/stockpot/development/terraform.tfstate")
    error_message = "Named workspace state must stay within the development prefix."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_acls && aws_s3_bucket_public_access_block.state.block_public_policy && aws_s3_bucket_public_access_block.state.ignore_public_acls && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "State must block all public access."
  }
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled" && aws_kms_key.state.enable_key_rotation && !aws_s3_bucket.state.force_destroy
    error_message = "State needs versioning, rotated encryption and no forced destruction."
  }
  assert {
    condition     = jsondecode(aws_iam_role.state["production_apply"].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:themasonbanks@79837282/stockpot@1319669903:environment:production"
    error_message = "Production trust must name exactly the approved repository/environment."
  }
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_role_policy.state["development_plan"].policy).Statement : !contains(statement.Action, "s3:PutObject") || alltrue([for resource in flatten([statement.Resource]) : !endswith(resource, "/terraform.tfstate")]) if statement.Effect == "Allow"])
    error_message = "Plan role must not overwrite state."
  }
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_role_policy.state["development_apply"].policy).Statement : alltrue([for resource in flatten([statement.Resource]) : !strcontains(resource, "/production/")]) if statement.Effect == "Allow"])
    error_message = "Development must not access production state."
  }
  assert {
    condition     = anytrue([for statement in jsondecode(aws_iam_role_policy.state["production_apply"].policy).Statement : contains(statement.Action, "iam:*") && contains(statement.Action, "sts:AssumeRole") && contains(statement.Action, "s3:DeleteObjectVersion") if statement.Effect == "Deny"])
    error_message = "CI roles must explicitly deny escalation and destructive recovery operations."
  }
}

run "legacy_subject" {
  command = plan
  variables {
    github_oidc_subject_prefix = "repo:themasonbanks/stockpot"
  }
  assert {
    condition     = jsondecode(aws_iam_role.state["production_apply"].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:themasonbanks/stockpot:environment:production"
    error_message = "Legacy repositories must retain their exact verified subject."
  }
}
run "reject_wildcard_subject" {
  command = plan
  variables {
    github_oidc_subject_prefix = "repo:themasonbanks/*"
  }
  expect_failures = [var.github_oidc_subject_prefix]
}
run "reject_other_repository" {
  command = plan
  variables {
    github_oidc_subject_prefix = "repo:themasonbanks/another-repository"
  }
  expect_failures = [var.github_oidc_subject_prefix]
}
run "reject_foreign_provider" {
  command = plan
  variables {
    existing_oidc_provider_arn = "arn:aws:iam::999999999999:oidc-provider/token.actions.githubusercontent.com"
  }
  expect_failures = [var.existing_oidc_provider_arn]
}
