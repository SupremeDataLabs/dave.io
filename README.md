# Dave.io / ask-dave

An AWS serverless chat application provisioned with Terraform. The supplied
`takehomeassignmentdave_io/index.html` is preserved unchanged; Terraform generates
the adjacent `config.js` with the deployed API URL.

## Status

The deployed website and chat are working. Submission checks listed below remain.

- Verified on AWS: unchanged frontend over HTTPS, generated configuration, and
  `GET /history` returning JSON with the correct CORS origin (initially empty).
- After API key rotation, the live `POST /chat` test returned HTTP 200 using
  `gpt-4.1-mini`. The earlier HTTP 502 was no longer reproduced. Its original
  cause was not conclusively identified.
- Verified that the successful test exchange was persisted and returned by
  `GET /history`, with the expected browser CORS origin.
- Browser verification: the project owner confirmed the website and chat work
  on October 2, 2026.
- CloudWatch logs and error alarms are provisioned. Alarm transitions,
  clean-account deployment, and complete teardown are not verified.
- GitHub Actions CI and approval-gated delivery are verified end to end on
  October 4, 2026: [successful release](https://github.com/SupremeDataLabs/dave.io/actions/runs/37228638822).
  Both jobs authenticated through OIDC; the saved no-change plan was stored
  privately in S3 and applied after production approval. Post-apply checks
  verified the unchanged HTTPS frontend, generated `config.js`, and `/history`.
  Application state is stored in private, encrypted, versioned S3 with locking.

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

The manual script requires an explicit mode: `--enable-chat` or `--history-only`.
The latter disables chat and removes its parameter; do not use it for an ordinary
release. For key rotation, also increment `TF_VAR_llm_key_version`.
Keep that version stable on later deployments; increment it only for another
rotation. Changing the version refreshes Lambda processes so they do not keep
using a cached old key. The version is not the secret itself.

## CI/CD: checks, plan, approval, apply

Pull requests run tests, a supplied-HTML checksum check, deterministic packaging,
and Terraform formatting/validation without cloud credentials. On `main`, the
delivery workflow runs those checks, creates a plan with the planning role,
waits for approval in the `production` environment, and applies the exact saved
plan. A workflow-wide concurrency group serializes releases without cancelling
an active apply. Remote S3 locking also coordinates CLI operations.

Official actions are pinned to commit SHAs. The workflow does not use
`pull_request_target`, expose AWS access to fork PRs, or store long-lived AWS
credentials in GitHub. The planning role trusts only this repository's `main`
branch. The deployment role trusts only its `production` environment; GitHub's
environment branch rule must restrict that environment to `main`.

### One-time activation (administrator)

1. Configure GitHub environment `production` with a required reviewer, only
   `main` allowed, and administrator bypass disabled. This is continuous delivery:
   a human reviews the plan and approves AWS changes. A solo maintainer must allow
   self-review; a team should require another reviewer.
2. Check whether the account already has the GitHub OIDC provider. In bootstrap,
   set `enable_delivery = true` and `github_repository` to your repository; set
   `existing_github_oidc_provider_arn` if reusing one. Set
   `github_oidc_subject_prefix` to the exact `sub_claim_prefix` returned by
   `gh api repos/OWNER/REPO/actions/oidc/customization/sub`. New repositories use
   immutable owner/repository IDs in this prefix; using the older name-only
   format causes AWS authentication to fail. See [GitHub's OIDC reference](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims).
   Keep these settings in a
   private local tfvars file on subsequent bootstrap runs. Review and apply
   bootstrap using an administrator identity permitted to create the new IAM
   roles, OIDC provider, and state bucket. The older restricted bootstrap
   permission set may need an administrator to authorize these new resources.
3. Freeze manual applies, back up local application state privately, and create
   the ignored `infra/remote-backend.tf.json` with the following configuration,
   replacing the bucket with bootstrap's `delivery_state_bucket` output:

   ```json
   {"terraform":{"backend":{"s3":{
     "bucket":"YOUR_STATE_BUCKET",
     "key":"application/terraform.tfstate",
     "region":"us-east-1",
     "encrypt":true,
     "use_lockfile":true
   }}}}
   ```

   Run `AWS_PROFILE=YOUR_DEPLOYMENT_PROFILE terraform -chdir=infra init -migrate-state`.
   Confirm the migration, compare state lineage/resource addresses before and
   after, and run a plan preserving the current key version. Do not initialize
   CI against empty state: it must manage the existing deployment, not a duplicate.
   The bootstrap stack itself remains locally managed; protect its state too.
4. Set repository variables `AWS_REGION`, `TF_STATE_BUCKET`, `AWS_PLAN_ROLE_ARN`,
   `AWS_DEPLOY_ROLE_ARN`, `LLM_MODEL`, and `LLM_KEY_VERSION`. The role ARNs come
   from bootstrap's `github_role_arns`. For the existing demo, the model is
   `gpt-4.1-mini` and the verified rotation version is `2`; fresh deployments must
   use their own existing version. No API key is a GitHub variable or secret.
5. Only after these checks, set `DEPLOYMENT_ENABLED=true`, dispatch **Deploy**
   on `main`, inspect the plan, approve `production`, and verify the smoke checks.

The state/plan bucket is private, encrypted, versioned, HTTPS-only, outside the
application provisioning bucket prefix, and protected against Terraform deletion.
Plans expire after three days and are not uploaded as public-repository Actions
artifacts. After expiry, create a new plan rather than attempting to approve it.

Routine releases reject deletes/replacements, disabled chat, and changes to the
SSM parameter. The CI roles explicitly deny parameter-value writes/deletion.
The AWS provider may read the existing key during refresh, but the nonempty
ephemeral write-only input keeps its value out of state/plan persistence; CI
never needs the user to supply the key. Treat plans and state as sensitive anyway.
Key rotation remains a manual operation; update `LLM_KEY_VERSION` afterward.
The apply job builds the same deterministic package from the same commit and
checks HTTPS, frontend bytes, configuration, and `/history`. It does not call
the paid model or prove browser behavior on every release.

Read-only planning still needs limited state-lock and private-plan writes. These
are operational storage permissions, not permission to modify the application.
The deployment role can update Lambda, so it is a trusted privileged identity;
OIDC alone is not a substitute for review and repository protection.

For interviews: explain the decisions as **untrusted PR checks → short-lived
planning credentials → reviewed plan → approval-gated deployment → smoke checks**.
The goal is repeatability and controlled change, not automation for its own sake.

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
python3 -m unittest discover -s scripts -p 'test_*.py'
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

If delivery is enabled, the state bucket is deliberately protected from bootstrap
destruction. Disable GitHub deployments first; remove the application, securely
archive/migrate state, and review retirement of the delivery bucket and roles
separately. Do not remove the state bucket while it is still the active backend.

## Scaling and remaining work

At 1,000 simultaneous users, API throttles, Lambda concurrency, OpenAI quotas,
and S3 history listing/read amplification need attention. A next iteration would
add authentication and per-user history, indexed/paginated history, spending
controls, improved redacted diagnostics, notification destinations for alarms,
and end-to-end validation of the approval-gated delivery workflow.

Before submission: verify browser history after reload and failure behavior,
rehearse a clean deployment and teardown, and record actual time spent.
Actual engineering time has not yet been recorded reliably.

Only this README, Terraform, application/test code, deployment scripts, workflow
configuration, Git ignore rules, and dependency files are published. The assignment PDF, private planning documents,
Graphify outputs, local configuration, state, and plans are intentionally omitted.
