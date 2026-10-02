# Dave.io / ask-dave

An AWS serverless chat application provisioned with Terraform. The supplied
`takehomeassignmentdave_io/index.html` is preserved unchanged; Terraform generates
the adjacent `config.js` with the deployed API URL.

## Status

Work in progress, not a completed assignment submission.

- Verified on AWS: unchanged frontend over HTTPS, generated configuration, and
  `GET /history` returning an empty JSON array with the correct CORS origin.
- Chat is enabled in the development deployment, but the live `POST /chat` test
  returned HTTP 502. Key-access/provider diagnostics and successful persistence
  verification remain outstanding.
- CloudWatch logs and error alarms are provisioned. Alarm transitions, browser
  interaction, clean-account deployment, and complete teardown are not verified.
- No CI/CD deployment workflow is configured yet.

## Architecture and choices

```text
Browser -> CloudFront (HTTPS) -> private S3 frontend bucket
Browser -> API Gateway HTTP API -> Python Lambda -> OpenAI API
                                      |-> SSM SecureString (API key)
                                      |-> private S3 history bucket
                                      |-> CloudWatch logs
API Gateway / Lambda metrics -> CloudWatch error alarms
```

CloudFront provides HTTPS without purchasing a domain. Its default certificate
does not provide a configurable minimum TLS policy. Origin access control keeps
the frontend bucket private. Separate buckets isolate frontend assets from chat
history. Lambda and HTTP API avoid always-on servers; S3 stores individual JSON
exchanges without an extra database. Storage and other usage can still incur
charges while the app is idle.

Lambda has a scoped execution policy and a permissions boundary. A separate
short-lived deployment role provisions resources. SSM stores the key encrypted;
Terraform uses an ephemeral variable and a write-only parameter value to avoid
saving it in state or plans. Never commit state, plans, credentials, or API keys.

## Prerequisites and bootstrap

- Terraform 1.11 or newer (below 2.0), Python 3.10+, and AWS CLI v2.
- Your own AWS account, authenticated CLI profiles, and funded OpenAI API key.
- An IAM Identity Center permission set allowed to assume the deployment role.
  Its name defaults to `ask-dave-TerraformAccess` and is configurable in bootstrap.

The application assumes the permissions boundary created by `infra/bootstrap`.
An administrator must bootstrap it first, using temporary credentials:

```bash
AWS_PROFILE=YOUR_BOOTSTRAP_PROFILE terraform -chdir=infra/bootstrap init
AWS_PROFILE=YOUR_BOOTSTRAP_PROFILE terraform -chdir=infra/bootstrap apply
```

Configure a deployment CLI profile to assume the output `deployment_role_arn`
using your Identity Center source profile. Do not use root access keys.
The local profile names `Dave.io` and `Dave.io-bootstrap` are examples, not
portable credentials. The default region is `us-east-1`; configure both stacks
consistently if changing it.

## Deploy

```bash
AWS_PROFILE=YOUR_DEPLOYMENT_PROFILE python3 scripts/deploy.py --enable-chat
```

Enter the OpenAI key at the hidden prompt, review the saved Terraform plan, and
confirm. The script builds Lambda, applies the plan, checks frontend bytes,
configuration and history, and prints the HTTPS URL. These readiness checks do
not prove that chat works. The default model is `gpt-4.1-mini`; set
`TF_VAR_llm_model` to configure it.

Omitting `--enable-chat` explicitly selects history-only mode; it is not a safe
way to preserve an already-enabled chat deployment. Keep `--enable-chat` on
subsequent deployments. For key rotation, also increment `TF_VAR_llm_key_version`.

## API and tests

- `POST /chat`: accepts `{"prompt":"Hello"}` and on success returns
  `{"prompt":"Hello","response":"...","timestamp":"..."}` after S3 persistence.
- `GET /history`: returns stored exchanges newest first, bounded to 80 records.
- History is shared across this public demo, not isolated per user. Do not submit
  private information. API throttling is not authentication or a spending cap.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.in
python3 -m unittest discover -s backend -p 'test_*.py'
```

## Destroy

The application teardown entry point is:

```bash
AWS_PROFILE=YOUR_DEPLOYMENT_PROFILE python3 scripts/destroy.py
```

Destruction permanently deletes chat history. The separate bootstrap is not
removed by that script; after application removal, destroy it explicitly:

```bash
AWS_PROFILE=YOUR_BOOTSTRAP_PROFILE terraform -chdir=infra/bootstrap destroy
```

Complete teardown is not yet rehearsed. Keep both local Terraform state files
securely until destruction is verified. Identity Center assignments created
outside these stacks are not removed by Terraform.

## Scaling and remaining work

At 1,000 simultaneous users, API throttles, Lambda concurrency, OpenAI quotas,
and S3 history listing/read amplification need attention. A next iteration would
add authentication and per-user history, indexed/paginated history, spending
controls, improved redacted diagnostics, notification destinations for alarms,
and CI with short-lived GitHub OIDC credentials rather than stored AWS keys.

Before submission: resolve the live chat failure, verify persistence/reload and
failure behavior, rehearse a clean deployment and teardown, and record actual
time spent. Actual engineering time has not yet been recorded reliably.

Only this README, Terraform, application/test code, deployment scripts, and
dependency files are published. The assignment PDF, private planning documents,
Graphify outputs, local configuration, state, and plans are intentionally omitted.
