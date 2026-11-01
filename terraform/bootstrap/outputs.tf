output "state_bucket" { value = aws_s3_bucket.state.id }
output "state_kms_key_arn" { value = aws_kms_key.state.arn }
output "state_role_arns" { value = { for key, role in aws_iam_role.state : key => role.arn } }
output "required_github_environments" { value = { for key, role in local.roles : key => role.environment } }
