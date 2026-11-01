terraform {
  required_version = ">= 1.17.1, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.71" }
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
variable "free_plan_eligibility_confirmed" {
  type    = bool
  default = false
}
variable "global_waf_region_approved" {
  type    = bool
  default = false
  validation {
    condition     = var.global_waf_region_approved
    error_message = "Owner review must approve us-east-1 for global CloudFront WAF."
  }
}
provider "aws" {
  region              = "eu-west-2"
  allowed_account_ids = [var.aws_account_id]
  default_tags { tags = { Environment = "production", ManagedBy = "Terraform" } }
}
provider "aws" {
  alias               = "global"
  region              = "us-east-1"
  allowed_account_ids = [var.aws_account_id]
  default_tags { tags = { Environment = "production", ManagedBy = "Terraform" } }
}
variable "production_release_approved" {
  type    = bool
  default = false
  validation {
    condition     = var.production_release_approved
    error_message = "A separate human production release approval is required."
  }
}
module "environment" {
  source                          = "../../modules/hosting_environment"
  providers                       = { aws = aws, aws.global = aws.global }
  aws_account_id                  = var.aws_account_id
  bucket_name                     = var.bucket_name
  free_plan_eligibility_confirmed = var.free_plan_eligibility_confirmed
  environment                     = "production"
}
output "preview_url" { value = module.environment.preview_url }
output "pricing_plan_status" { value = module.environment.pricing_plan_status }
output "pricing_plan_arn" { value = module.environment.pricing_plan_arn }
output "environment" { value = module.environment.environment }
