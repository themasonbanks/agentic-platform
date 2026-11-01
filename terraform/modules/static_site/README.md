# Static web hosting proposal

This module creates a private versioned S3 origin and CloudFront distribution using signed origin access control. It exposes only GET/HEAD via an HTTPS viewer endpoint and denies insecure S3 transport. No database, VPC, NAT gateway, load balancer, domain registration or customer-managed KMS key is added. CloudFront is a global service; the reviewed calling root must explicitly configure its S3 provider in London eu-west-2 with allowed_account_ids. This module does not itself authenticate or select an account.

Stockpot currently has `apps/mobile`'s export:web script using Expo export --platform web. Its browser export is a preview of the mobile project; it does not prove native secure storage, deep links, keyboard behaviour or device accessibility. The exact export/base URL, Supabase redirect allowlist and deployed auth flow need phone/browser verification. This module intentionally does not map every missing asset to HTML or claim SPA routing support without that check.

## Controlled delivery

Use separate development/production state roots and the reviewed OIDC environment gates from the state bootstrap. The state roles currently have no application deployment permissions. Prepare a separate least-privilege deployment policy and real plan; require owner approval before credentials/apply. Production must accept only protected main with a human environment gate. No workflow here applies infrastructure.

Build on an unprivileged hosted runner. Inject only reviewed public client configuration into the export; never expose service-role keys or admin/source-map tokens. Sentry DSN/environment wiring is pending KEB-126: server-only secrets belong in managed secret storage and privileged release steps, while browser-visible values must be treated as public. Do not put secret values into Terraform variables or state. Domain/certificate and strict TLS-policy requirements need explicit review; the default CloudFront certificate is used here, not a custom domain.

A future preview controller must associate each preview with PR/head, apply only after trusted-code review and record usage/cost attribution. On PR closure it should remove approved preview artifacts and disable exposure, then request manual destruction of infrastructure/state after retention and recovery review. Versioned object cleanup must be explicit; force_destroy is false. Do not automatically delete production or recovery history. The exact cleanup path, CI checks, deployment policy and controller remain unimplemented.

## Costs and acceptance

No resources have been created. Before applying, review current S3/CloudFront request, storage, transfer, invalidation and retention charges against Mason's approximate £5/month target / £10 upper alert. Do not infer a hard cap or free service. Prefer one small reviewed development deployment before multiplying per-PR distributions. Pricing references: https://aws.amazon.com/s3/pricing/ and https://aws.amazon.com/cloudfront/pricing/ .

The offline mock test asserts all public-access blocks, distribution-bound OAC trust, signed requests, versioning/no forced destruction and HTTPS redirection. It does not verify actual IAM, TLS, caching, accessibility, export content or a reachable phone preview. Live deployment, secret integration, preview lifecycle, controlled dev/prod releases and cost logging remain required before KEB-125 is Done.

Origin policy follows https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html .
