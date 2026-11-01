# Reviewed AWS state bootstrap (not provisioned)

Region: London eu-west-2. Mason's development target is approximately £5/month, with an upper alert around £10. No AWS resources have been created and no authenticated AWS session is available. This root creates only versioned/private encrypted state storage, a rotating KMS key and scoped GitHub OIDC state-access roles. It creates no EC2, RDS, load balancer or NAT gateway.

A customer-managed KMS key has a recurring key charge plus request/storage costs; review current London pricing and billing currency before applying. This is a cost proposal, not a spending cap or guarantee. State versions can grow; monitor usage and do not delete recovery history automatically. References: https://aws.amazon.com/kms/pricing/ and https://aws.amazon.com/s3/pricing/ .

## Owner-only bootstrap

1. Complete KEB-123: root MFA, separate admin sign-in, budget notifications and region confirmation. Authenticate with a temporary owner session; never paste keys into chat or commit them.
2. Check whether the account already has the GitHub OIDC provider. Supply existing_oidc_provider_arn if so; IAM permits only one provider per issuer. If omitted, this reviewed root creates one. AWS retrieves/verifies the issuer thumbprint when not supplied; no copied stale thumbprint is hardcoded.
3. Verify the repository OIDC prefix with `gh api repos/OWNER/REPO/actions/oidc/customization/sub`. Stockpot currently returns `use_immutable_subject: true` and `sub_claim_prefix: repo:themasonbanks@79837282/stockpot@1319669903` (checked 1 November 2026). Set github_oidc_subject_prefix to that exact verified prefix; new repositories use immutable IDs, while older repositories may use repo:owner/name. Custom claim layouts are outside this bootstrap. Never guess or add wildcards. See https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws . Copy terraform.tfvars.example to a local ignored terraform.tfvars, using the reviewed account ID and globally unique bucket name. provider allowed_account_ids prevents applying to another account.
4. With Terraform1.17.1, run terraform init, terraform plan -out=bootstrap.tfplan and review the full plan/costs. Mason must explicitly approve the provider trust and bootstrap apply. No automated job runs apply here. Local bootstrap state/plan files are private, ignored and must be stored/recovered securely.
5. After approved bootstrap, create the four GitHub environments from outputs. Restrict production and production-plan to protected main, require Mason's review, and ensure untrusted PR code cannot run with state credentials. Development PR plans also need explicit trusted-code review before access. An AWS environment subject does not by itself enforce branch approval; GitHub environment rules are mandatory.
6. Test OIDC roles with temporary credentials. Backend examples use native S3 lockfiles (Terraform>=1.10); no DynamoDB table or standing admin key is needed. Every environment has its own state path. Plan roles may write/delete only their lockfile; apply roles may write state but never delete state versions. None have application deployment or IAM-admin permissions.
7. Migrate bootstrap state only after owner review: copy backend.tf.example to the ignored backend.tf, use a separate bootstrap state key accessible only to the owner and run terraform init -migrate-state. Keep offline backup until versioned remote recovery is verified. Application roles cannot access bootstrap state.

## Recovery and rollout evidence

Before enabling deployment, verify backend init/plan with temporary credentials, concurrent lock rejection, bucket versioning, old-version recovery without deleting history, development/prod separation and feature-branch denial of production access. These require live AWS/GitHub setup and are not established by mocks.

prevent_destroy protects bucket/key in Terraform; removal of configuration can bypass that lifecycle protection, so destroy/removal remains owner-only and reviewed. Role policy explicitly denies IAM/account/organization changes, role chaining, bucket/version destruction and KMS key administration. This does not replace account-level admin boundaries.

## Offline checks

terraform init -backend=false installs the pinned provider lock; terraform validate checks schema; terraform test uses mock_provider aws only. Its command=apply is a mocked lifecycle, not an AWS apply. Tests assert public-access blocking, versioning/key rotation, exact production trust, plan state immutability, dev/prod separation and denied escalation. They do not prove live IAM or locking behaviour.

Cost reference checked 1 November 2026: AWS lists USD1/month per customer-managed KMS key, plus USD1/month for each of the first two rotations (then capped), requests and S3 usage. Currency conversion, taxes and retention affect actual GBP billing. AWS-managed keys have no key-storage fee but offer less separate lifecycle/policy control; this draft proposes a customer-managed key for state control and requires owner cost approval before apply. No budget guarantee is implied.
