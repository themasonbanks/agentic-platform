variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Supply the reviewed twelve-digit AWS account ID."
  }
}
variable "region" {
  type    = string
  default = "eu-west-2"
  validation {
    condition     = var.region == "eu-west-2"
    error_message = "This bootstrap is reviewed for London only."
  }
}
variable "state_bucket_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "Supply a unique DNS-compatible state bucket name."
  }
}
variable "github_repository" {
  type    = string
  default = "themasonbanks/stockpot"
  validation {
    condition     = can(regex("^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use one explicit owner/repository, without wildcards."
  }
}
variable "github_oidc_subject_prefix" {
  type = string
  validation {
    condition = var.github_oidc_subject_prefix == "repo:${var.github_repository}" || can(regex(
      "^repo:${split("/", var.github_repository)[0]}@[0-9]+/${replace(split("/", var.github_repository)[1], ".", "[.]")}@[0-9]+$",
      var.github_oidc_subject_prefix
    ))
    error_message = "Use the verified repo:owner/repository prefix, with both immutable IDs when enabled; wildcards and other repositories are forbidden."
  }
}
variable "state_prefix" {
  type    = string
  default = "stockpot"
  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.state_prefix))
    error_message = "State prefix must not contain path separators or wildcards."
  }
}
variable "name_prefix" {
  type    = string
  default = "agentic-stockpot"
  validation {
    condition     = can(regex("^[a-z0-9-]{1,38}$", var.name_prefix))
    error_message = "Use a short lowercase role prefix."
  }
}
variable "existing_oidc_provider_arn" {
  type     = string
  default  = null
  nullable = true
  validation {
    condition     = var.existing_oidc_provider_arn == null || var.existing_oidc_provider_arn == "arn:aws:iam::${var.aws_account_id}:oidc-provider/token.actions.githubusercontent.com"
    error_message = "Use an existing GitHub OIDC provider ARN or null for reviewed creation."
  }
}
