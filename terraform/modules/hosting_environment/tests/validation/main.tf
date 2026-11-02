terraform {
  required_version = ">= 1.17.1, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.71" }
  }
}
provider "aws" {
  region              = "eu-west-2"
  allowed_account_ids = ["000000000000"]
}
provider "aws" {
  alias               = "global"
  region              = "us-east-1"
  allowed_account_ids = ["000000000000"]
}
module "subject" {
  source                          = "../.."
  providers                       = { aws = aws, aws.global = aws.global }
  aws_account_id                  = "000000000000"
  bucket_name                     = "schema-validation-fixture"
  environment                     = "development"
  free_plan_eligibility_confirmed = true
}
