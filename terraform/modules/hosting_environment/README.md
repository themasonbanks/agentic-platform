# Shared hosting environment composition

This module composes one static-site distribution/FREE subscription, its dedicated global WAF and a separate London content KMS key/policy. environment is restricted to development or production and is included in resource descriptions/names. Each calling root must have its own backend key, bucket name, content key and WAF; do not use workspaces to bypass production state/identity separation.

Pass reviewed aws (eu-west-2) and aws.global (us-east-1) provider configurations, both restricted to the intended account. The calling root enforces owner confirmation of the global-service exception; the static-site child enforces FREE eligibility confirmation. These input booleans are preflight acknowledgements, not an authentication or approval system. Use the example environment roots and protected deployment identities; this module does not grant IAM roles or run applies.

The WAF blocks above2000 requests/IP over300seconds, with aggregate metrics and no sampled request payloads. Review the default against real shared-IP browser traffic. KMS rotation and a30-day recovery window are enabled, and deletion is guarded. CloudFront decrypt permission is bound to the exact distribution ARN. No application secrets or database resources are included.

The static-site child uses AWS Managed-CachingOptimized because FREE plans do not support custom caching rules. Upload headers/invalidation remain a deployment requirement. The preview URL cannot be returned until the plan is ACTIVE; failed provisioning/cancellation can leave billable resources. See the child README for remaining costs and live acceptance gaps.

Offline tests verify exact distribution decrypt trust, key rotation/recovery, WAF controls, production naming and rejection of unknown environments. Mocked apply never calls AWS. Live provider/account/region, IAM, eligibility, billing and traffic enforcement remain unverified.
