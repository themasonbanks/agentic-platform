terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "agentic-platform", ManagedBy = "terraform" }
  }
}

resource "aws_kms_key" "state" {
  description             = "Terraform state encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket" "state" {
  bucket        = var.state_bucket_name
  force_destroy = false
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }, {
      Sid      = "DenyExplicitNonKMS", Effect = "Deny", Principal = "*", Action = "s3:PutObject"
      Resource = "${aws_s3_bucket.state.arn}/*"
      Condition = {
        Null            = { "s3:x-amz-server-side-encryption" = "false" }
        StringNotEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" }
      }
      }, {
      Sid      = "DenyExplicitOtherKey", Effect = "Deny", Principal = "*", Action = "s3:PutObject"
      Resource = "${aws_s3_bucket.state.arn}/*"
      Condition = {
        Null            = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = "false" }
        StringNotEquals = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = aws_kms_key.state.arn }
      }
      }, {
      Sid      = "DenyExplicitKMSWithoutKey", Effect = "Deny", Principal = "*", Action = "s3:PutObject"
      Resource = "${aws_s3_bucket.state.arn}/*"
      Condition = {
        StringEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" }
        Null         = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = "true" }
      }
    }]
  })
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.existing_oidc_provider_arn == null ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  oidc_provider = coalesce(var.existing_oidc_provider_arn, try(aws_iam_openid_connect_provider.github[0].arn, null))
  roles = {
    development_plan  = { environment = "terraform-development-plan", state = "development", write = false }
    development_apply = { environment = "development", state = "development", write = true }
    production_plan   = { environment = "terraform-production-plan", state = "production", write = false }
    production_apply  = { environment = "production", state = "production", write = true }
  }
}

resource "aws_iam_role" "state" {
  for_each             = local.roles
  name                 = "${var.name_prefix}-${replace(each.key, "_", "-")}"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Federated = local.oidc_provider }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "${var.github_oidc_subject_prefix}:environment:${each.value.environment}"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "state" {
  for_each = local.roles
  role     = aws_iam_role.state[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.state.arn
        Condition = { StringLike = { "s3:prefix" = ["${var.state_prefix}/${each.value.state}/*"] } }
      },
      {
        Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = aws_s3_bucket.state.arn
      },
      {
        Effect = "Allow", Action = each.value.write ? ["s3:GetObject", "s3:PutObject"] : ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.state.arn}/${var.state_prefix}/${each.value.state}/terraform.tfstate",
          "${aws_s3_bucket.state.arn}/${var.state_prefix}/${each.value.state}/workspaces/*/${var.state_prefix}/${each.value.state}/terraform.tfstate"
        ]
      },
      {
        Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${aws_s3_bucket.state.arn}/${var.state_prefix}/${each.value.state}/terraform.tfstate.tflock",
          "${aws_s3_bucket.state.arn}/${var.state_prefix}/${each.value.state}/workspaces/*/${var.state_prefix}/${each.value.state}/terraform.tfstate.tflock"
        ]
      },
      {
        Effect    = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource  = aws_kms_key.state.arn
        Condition = { StringEquals = { "kms:ViaService" = "s3.${var.region}.amazonaws.com" } }
      },
      {
        Effect   = "Deny"
        Action   = ["iam:*", "organizations:*", "account:*", "sts:AssumeRole", "s3:DeleteBucket", "s3:DeleteObjectVersion", "kms:DisableKey", "kms:ScheduleKeyDeletion", "kms:PutKeyPolicy"]
        Resource = "*"
      }
    ]
  })
}
