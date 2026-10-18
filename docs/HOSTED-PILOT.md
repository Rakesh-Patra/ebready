# Hosted application-deployment pilot

The hosted worker uses the same deployment flow previously tested locally.
Access requires a verified InsForge email and membership in the existing operator
allowlist, stored privately in Secrets Manager. Email verification alone does not
authorize spending the operator's AWS account.

## Hackathon verification

1. Open the HTTPS EBKit URL and sign in with an existing approved, verified account.
2. Switch to Live and create a project in Ohio (us-east-2).
3. For a small public test, use https://github.com/traefik/whoami with port 80.
   Its root Dockerfile builds a small HTTP service. Confirm the AWS cost notice.
4. Watch the build/deployment log, then open the resulting HTTPS application URL.
5. Destroy the test application using its exact displayed environment name.
6. Verify cleanup completes. The shared cluster continues running EBKit itself.

Email roles are copied separately from the local server configuration: ordinary
approved users receive one 30-minute trial, repeat testers retain their testing
quota, evaluators receive manual-lifetime access, and designated paid-test users
can select a two-hour pass. Roles come from the authenticated identity, never
browser-supplied privilege flags. Timed plans retain one replica and expiry cleanup.
The hosted region remains us-east-2. Existing trial claims remain in the database.
GitHub automatic deployment and managed database provisioning remain disabled.

Run `python scripts/store_runtime_secrets.py` to validate the local role settings
without contacting AWS or printing addresses. Add `--store` to synchronize them
to separate Secrets Manager secrets and merge their references into the ignored
Terraform inputs. Define all four email lists, including empty values to revoke
unused roles; every privileged address must also be approved. Then review/apply
Terraform and deploy the tested image. `EBKIT_STRICT_CAPACITY=true` is an optional
operator-wide capacity override; it is disabled for local role parity.

Application permission checks and AWS IAM permissions are separate. The current
hosted policy covers the core build/deployment lifecycle. Advanced commands that
need additional AWS services are not yet verified in the hosted environment;
local availability alone does not establish that they work on AWS.

## Verification on 18 October 2026

The local role configuration was synchronized to four separate Secrets Manager
entries and applied through Terraform. The hosted HTTPS health endpoint reported
`liveEnabled: true`; unauthenticated trial requests returned HTTP 401.

A disposable public sample was built with CodeBuild, pushed to ECR, and deployed
to Beanstalk Cluster using a temporary STS role with the exact worker provisioning
policy. It reached Ready/Green and returned HTTP 200 over verified HTTPS. These
commands exited successfully against that real environment: `status`, `logs`,
`versions`, `resources`, `events`, `health`, `options`, `config`, and `scale`.
The scaling check retained one replica.

`destroy` then removed the sample environment, ECR repository/images, CodeBuild
project/log group, empty application/versions, and eligible environment streams.
`cleanup-status --json` confirmed `cleanup_complete: true`,
`environment_deleted: true`, and `shared_cluster_retained: true`. The EKS cluster
hosting EBKit was retained. Do not delete shared infrastructure for tenant cleanup.

Local validation passed 233 frontend/server tests, 33 targeted Python checks, the
production UI build, and Terraform validation. Raw AWS receipts remain in ignored
local artifacts because they contain account/resource metadata. This exercise did
not sign in as each real user or verify advanced RDS/budget integrations.

The hosted Google login initially failed because the HTTPS callback was absent
from the actual EBKit backend allowlist. A CLI connection in another directory
pointed to a different backend, so editing that project's config did not fix EBKit.
The current official InsForge CLI was used with an isolated connection matching
the deployed `INSFORGE_URL`. Its reviewed plan added only the hosted callback and
preserved all four local callbacks. The apply reported no skipped fields. A real
request through EBKit's `/insforge/api/auth/oauth/google` then returned HTTP 200
with a Google authorization URL. Compare the CLI backend URL with the app's URL
before applying auth settings; never commit CLI connection files or admin keys.

All deployed role secret values were compared privately with local configuration:
two approved accounts, two repeat testers, one evaluator, and one paid-test account.
The deployed CloudFormation secret references matched all four lists. Accounts
keep their own role; approval does not automatically grant evaluator privileges.

An additional 78 HTTP-worker regression cases compare local and hosted behavior
for those role combinations. They cover status, logs, versions, resources, events,
health, options, configuration, rollback, scaling, version deletion, destruction,
timed-plan scaling restrictions, and cross-owner rejection. Together with the
trial/hosted-worker suite, all 102 targeted tests passed. These tests use synthetic
authenticated identities and a mocked CLI process; they verify authorization and
command dispatch, not a real sign-in as either account or every underlying AWS API.

## AWS permissions and secrets

The runtime uses EKS Pod Identity, not stored AWS access keys. Its provisioning
permissions cover only generated ebk-* applications, their images/build projects
and private source uploads in this account/region. It cannot administer its own
role, change the shared EKS roles, read Terraform state or deploy over EBKit itself.
Cluster/node/observability roles are pre-provisioned and reused.

Each untrusted source build gets a repository-specific CodeBuild role with an
operator-controlled permissions boundary. The worker can create/update only build
roles with that boundary. Source builds receive no Gemini key, InsForge admin key,
operator approval list, IAM-write permission or permission to read worker state.

Gemini, InsForge administration and approved-email values remain in Secrets
Manager. Only their references enter infrastructure configuration. The root image
includes a checksum-verified AWS CLI for Cluster APIs absent from the SDK model.

## Restart and cleanup behavior

A single worker replica uses Recreate updates to avoid overlapping workers.
Deployment metadata, including build identifiers, is synchronously saved to a
private encrypted S3 prefix separately from Terraform state. This lets a new pod
identify and clean up interrupted work. Source ZIP preparation and local job
journals are ephemeral; a pod loss can interrupt a job and require review/reupload.
This is a supervised hackathon pilot, not a highly available multi-tenant service.
Do not run an independent local live worker against the same database concurrently.

CI refuses deployment while the worker reports an active operation. Avoid changing
runtime configuration during a tenant deployment. Timed cleanup is driven by the
persistent trial database; failed cleanup retains the slot and retries. Hosted
cleanup verifies the tenant environment and eligible artifacts are gone without
waiting for the shared control-plane cluster to be deleted.

Three old failed records were reconciled only after verifying that their AWS
environments, images and source uploads were absent and no build was running.
Their historical CodeBuild projects/logs and the database rows were retained.
