# Cluster Mode migration

The dashboard now runs in Elastic Beanstalk Cluster Mode, replacing the earlier
Standard single-instance deployment at the owner's request. Cluster Mode provisions an
HTTPS listener and an ACM certificate for its own environment domain; a custom
domain is optional. HTTP redirects to HTTPS.

## Verified release: 17 October 2026

- [Open the HTTPS application](https://ebkit-dashboard-practice-cluster.eba-cktxxx2w.us-east-2.elasticbeanstalk.com).
- [Successful OIDC workflow](https://github.com/Rakesh-Patra/ebready/actions/runs/39055100638): security, verify and deploy passed.
- Source commit: `5a5fa3e62e8c4f2a4a6c1f2e27208fddeada490d`.
- Image digest: `sha256:3433e9812618a3d2be611680ad9cc90867421f4c3a785d894cc3108c5eef5e87`.
- Release: `release-5a5fa3e62e8c-39055100638-1`, Ready / Green.
- 65 targeted Python tests and 227 frontend tests passed.
- Trusted TLS, HTTP-to-HTTPS redirect, health and built assets verified.
- Login is enabled; a real browser submitted a nonexistent test identity through
  the SDK and received normal invalid-credentials validation (401). An empty
  same-origin request received field validation (400). No account was created.
- Replica, memory and CPU metrics contain actual CloudWatch samples; both Cluster
  alarms are OK. No alarm notification subscriptions are configured.

The first OIDC update rolled back cleanly after CloudFormation reported missing
ListTagsForResource permission. Granting that read action on only this environment
resolved the failure. The successful final run above is the delivery evidence.

## Infrastructure ownership

The pinned Terraform AWS provider accepts only WebServer and Worker tiers. The
current upstream provider source also lacks Cluster. Terraform therefore owns a
named CloudFormation stack whose AWS::ElasticBeanstalk::Environment uses
Tier Name=Cluster, Type=EKS. This avoids pretending that the unsupported native
Terraform environment resource can configure Cluster Mode.

The stack reuses the account's existing cluster, node and observability roles. A
separate application role uses EKS Pod Identity and can read only the two configured
runtime secrets. Neither AWS keys nor secret values enter Terraform. The dashboard
starts with one replica, a 250m CPU request, 512Mi memory request and 1Gi limit.
Readiness and ALB health checks use /api/health. Node and cluster capacity remain
service-managed. Cluster Mode adds EKS, load balancer and custom-metric costs.

## Safe migration sequence

1. Register the previously scanned immutable ECR image as a Cluster application version.
2. Apply a creation-only Terraform plan while Standard continues serving the demo.
3. Verify the new endpoint's publicly trusted TLS certificate and HTTP redirect.
4. Enable login through Cluster runtime configuration, verify the sign-in dialog
   and upstream authentication validation without creating a test account.
5. Update GitHub's environment/stack variables and the OIDC role's scoped permissions.
6. Run the full security, verification and OIDC delivery pipeline against Cluster.
7. Remove the Standard environment, its instance profile and obsolete IAM/alarms
   through a reviewed Terraform plan after the replacement works.
8. Check the final Terraform plan, active environment, logs, metrics and login again.

## Subsequent delivery

CI installs pinned AWS CLI 2.37.10 with a verified SHA256 because the installed
Python SDK can predate ImageConfiguration. It registers the scanned image directly,
then updates only the CloudFormation VersionLabel parameter using the previous
template. Terraform ignores drift only for that version parameter, so its reviewed
runtime configuration remains authoritative. CI verifies HTTPS, health, frontend
assets and an empty sign-in request reaching authentication validation.

Login is distinct from permission to spend AWS resources. Live deployment and
GitHub automatic deployment remain disabled during this migration; the application
role has no customer-infrastructure provisioning permission. Do not broaden that
role merely to make a trial button execute cloud operations.

## Observability

The dashboard and alarms use Cluster's ElasticBeanstalk/Infrastructure namespace:
EnvironmentReplicas, container_memory_working_set_bytes and the cumulative CPU
counter. Replica and memory alarms replace Standard EC2 alarms. Application logs
are in /aws/elasticbeanstalk/application/logs, separated by the environment's stream
prefix. Shared Cluster log groups must not be deleted with an individual application.
Set seven-day retention only after reviewing other users of those shared groups.
Alarm notifications and application traces require further configuration.

## References

- [Cluster networking and HTTPS](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/command-options-general-eks.html)
- [Cluster IAM and Pod Identity](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/beanstalk-cluster-permissions.html)
- [Cluster monitoring and metric dimensions](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/monitoring-cluster-environments.html)

## Scanner exceptions

The current Terraform scan passes 43 checks with 14 resource-local skips. Four
release/state-bucket exceptions and one ECR exception preserve the original
encryption/single-region/auditing decisions. One stack exception records that no
SNS recipient is configured. Each of the four shared log groups has two explicit
exceptions: AWS-managed encryption rather than customer KMS, and seven days of
operational retention rather than a one-year audit archive. No blanket skip or
soft-fail is used. Docker and Actions have no exceptions.

Browser testing found that InsForge SDK 1.5.2 resolves absolute /api paths, dropping
a base URL's /insforge prefix. A scoped SDK fetch transport now rewrites only
same-origin authentication/database calls to the proxy. Tests exercise the real
SDK and ensure unrelated application routes and external URLs stay unchanged.
Deployment capture tests a nonexistent example.invalid account, expects normal
credential validation, and never creates an account or submits real credentials.

## Completed Standard removal

After the successful OIDC release and real browser login check, Terraform removed
the Standard environment and its dedicated EC2 profile, IAM roles/policies and
obsolete alarms. The reviewed cutover applied 3 additions, 5 updates and exactly
9 deletions; the healthy Cluster stack was unchanged. Shared Cluster log groups
are retained on application destruction, with seven-day retention configured.

Final read-back confirmed only the Cluster environment is active for this app, both
alarms are OK and all four shared log groups retain seven days. The final full
Terraform plan reported no changes after CI changed the image version.
