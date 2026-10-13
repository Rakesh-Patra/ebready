# Production operations and live verification

These integrations extend EBKit beyond deployment. Database provisioning, CI/CD, alerts and billing use RDS, GitHub Actions, CloudWatch and AWS Budgets respectively. They require service permissions and configuration; they are not all native EB CLI features.

Run `ebkit operations ACTION --app APP --environment ENV --file settings.json`. Read operations do not require `--yes`; changes do. The web exposes the same actions under Settings ? Cloud controls ? Production operations. Advanced writes require designated access; persistent resources are unavailable in short free trials.

## External PostgreSQL

`database-create` accepts:

```json
{"vpcId":"vpc-123abc","subnetIds":["subnet-aaa","subnet-bbb"],"applicationSecurityGroup":"sg-123abc","instanceClass":"db.t4g.micro","storageGb":20,"multiAz":false}
```

The subnets must span two availability zones in the same VPC. Creation uses a private, encrypted PostgreSQL instance, seven-day backups, deletion protection, a dedicated security group and Secrets Manager-managed credentials. `database-network` grants port 5432 only from the supplied application security group. `database-status` reports readiness; `database-attach` merges DATABASE_URL into AWS environment configuration without printing credentials. Reattach after rotating the managed password.

`database-check` executes SELECT 1. Install `ebkit[database]` and run the CLI/worker with a route into the private VPC. `database-migrate` accepts reviewed SQL:

```json
{"migrations":[{"id":"001-create-notes","sql":"CREATE TABLE notes (id BIGINT PRIMARY KEY, body TEXT NOT NULL)"}]}
```

Migrations run with a PostgreSQL advisory lock and checksum ledger. Applied migrations cannot silently change. SQL cannot control transactions or modify the ledger. Snapshot first with `database-snapshot`. Automatic application rollback does not undo schema migrations.

Databases survive environment deletion. `database-delete` requires `databaseConfirmation` matching the managed database identifier and retains a final snapshot and automated backups. Network resources require separate cleanup. Production instance sizing and Multi-AZ availability must be reviewed before creating the database.

## Tests, OIDC and approvals

`pipeline` accepts a deployment `roleArn`, optional `cliSource`, a reviewed `cliRef`, optional exact `oidcSubject`, and `releasePolicy`. It returns workflow YAML, the release policy, IAM trust JSON and setup instructions. Commit the YAML as .github/workflows/ebkit.yml and the policy as .ebkit/release.json. Create the GitHub OIDC provider, configure the role's deployment permissions, verify the exact OIDC subject and configure required reviewers on the named GitHub environment. Naming an environment alone does not configure approval protection. Newer repositories may include IDs in OIDC subjects.

The test job has no AWS credentials; deployment requires the test job, acquires short-lived AWS credentials via OIDC and deploys the specific GitHub SHA. A generated web pipeline disables that project's polling watcher, preventing CI approval bypass. Use a reviewed commit for cliRef before production. The generated files must be committed by the repository owner.

A reviewed release policy can also gate direct/automatic deployments:

```json
{"testCommands":[["npm","ci"],["npm","test"]],"healthPath":"/health","expectedText":"ok","autoRollback":true,"attempts":6}
```

Pass `--release-policy FILE` to deploy, or use the web's Verified release control. Commands must include dependency installation needed by the application and be supported by the CodeBuild image. Tests run before image build/push. Verification checks the AWS version label, Ready/Green state and HTTP response. Recovery restores only the prior version and will not overwrite a concurrently deployed newer version. The first deployment has no previous version to restore. HTTP probes reject redirects and arbitrary browser-supplied hosts.

## Monitoring, alerts and autoscaling

`metrics` accepts `{"hours":24}` and retrieves CloudWatch history. CPU is a counter rate in cores, memory is bytes and replicas are ready replicas. ALB deployments also report requests, target 5xx errors and p95 latency. Missing samples are not shown as zero. The web renders history charts from returned samples.

`alarm` accepts `{"metric":"errors","threshold":5,"topicArn":"arn:aws:sns:REGION:ACCOUNT:TOPIC"}`. Confirm SNS subscriptions separately. `autoscale` accepts `{"minimum":1,"maximum":5,"cpuPercent":75,"memoryPercent":80}`. Percentages refer to resource reservations; multiple triggers select the highest required replica count. Recent events help explain policy updates.

## Costs, budgets and retained resources

`cost` accepts `{"days":7}` and retrieves environment-tagged Cost Explorer totals, a past-spend monthly run-rate extrapolation, and AWS's forecast for the remaining current month. `remainingMonthForecastUsd` is `null` when Cost Explorer has insufficient forecast data; permission or other API errors are reported. Activate the environment cost allocation tag. Shared, untagged and recent charges can be missing; this is not a complete account bill or guaranteed price quote. No observed cost is reported as unknown, not zero.

`budget` accepts `{"monthlyUsd":10,"email":"owner@example.com"}` and creates an environment-tagged monthly budget with an 80% email alert. Budget alerts do not stop spending. `leftovers` inventories application-tagged retained resources. RDS, snapshots, images and shared infrastructure may remain billable; no resources are automatically deleted by this report.

## Tomorrow's live test checklist

1. Verify credentials and IAM permissions in a test account.
2. Build/run/scan a sample image, then deploy it and confirm its URL.
3. Push a visible change; check the same URL, settings and pinned version.
4. Make application tests fail; ensure no environment update occurs.
5. Make the release health check fail; verify previous-version recovery.
6. Check metrics, confirmed SNS alerts, scaling targets and missing-data behavior.
7. Create a small private database; verify SG routing, SELECT 1, migration replay/checksum rejection and snapshot creation.
8. Confirm GitHub OIDC subject, role permissions and required-reviewer gate before using the workflow.
9. Activate cost tags, confirm Cost Explorer coverage and budget notifications.
10. Destroy disposable environments, inspect retained resources and explicitly clean databases/snapshots after review.

Automated checks are local/mocked AWS checks. Live integration verification remains separate; complete it before presenting the integrations as production-verified.
