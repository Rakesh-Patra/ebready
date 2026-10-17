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

Approved repeat testers receive timed trials rather than an unlimited evaluator
bypass. The hosted worker permits one active/reserved/cleanup-pending tenant at a
time and one replica in us-east-2. Existing trial claims remain in the database.
GitHub automatic deployment and managed database provisioning remain disabled.

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
