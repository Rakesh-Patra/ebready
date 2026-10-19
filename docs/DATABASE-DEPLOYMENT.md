# Database setup before deployment

EBKit's InsForge database stores the dashboard's accounts and deployment records.
It is separate from the database used by a deployed application.

## Existing database (available on trials)

The deployment wizard shows the connection variable detected in the source, such
as `POSTGRES_URL`. Paste the existing provider's connection URL into that field.
The same value appears in the application's environment settings and is sent only
when deployment starts. Values are not stored in the project database. Apps that
do not require a database can leave the optional database section empty.

URLs must have a supported scheme and host. Localhost/loopback URLs are rejected:
they refer to the deployed container, not the user's computer. Static validation
does not prove connectivity, credentials, network routing, or schema readiness.
Local SQLite storage is not supported as a durable hosted database.

## Create PostgreSQL before the app starts

This is available only for approved accounts **without a trial deadline**, with an
operator-configured network template for the selected region. It is not enabled
for free trials or the two-hour pass. The website never accepts AWS credentials
or user-selected infrastructure templates.

Set `EBKIT_DATABASE_TEMPLATES_FILE` in the backend environment to the absolute path
of an operator-reviewed JSON file. Example:

```json
{
  "us-east-2": {
    "clusterName": "YOUR_EXISTING_BEANSTALK_EKS_CLUSTER",
    "subnetIds": ["subnet-REPLACE_A", "subnet-REPLACE_B"],
    "instanceClass": "db.t4g.micro",
    "storageGb": 20,
    "multiAz": false
  }
}
```

Replace every example with real infrastructure. `clusterName` must identify an
existing ACTIVE **Beanstalk-managed** EKS cluster. The deployment uses that
cluster's exact application subnet selection so Beanstalk can reuse its cluster.
Database subnets must be in the same VPC and span at least two availability zones.
The cluster security group is granted PostgreSQL access on the owned database
security group; access is never opened to the internet. The operator must review
routing, network ACLs, DNS, cluster identity, permissions and capacity beforehand.
This feature does not bootstrap an EKS cluster or VPC for the operator.

In the normal backend with trials enabled, only `EBKIT_EVALUATOR_EMAILS` accounts
can use this path. In a backend explicitly configured with `trialsEnabled:false`,
designated testing accounts can also use it. Existing allowlist checks still apply.
Restart the backend after changing its templates or approved accounts. When no
template/access exists, the wizard explains why creation is unavailable and keeps
the existing-URL path available.

The user selects **Create AWS PostgreSQL before deployment** and explicitly
acknowledges separate, persistent RDS charges. The worker builds the image first;
only after a successful build does it create private encrypted PostgreSQL with
RDS-managed credentials, wait for availability, and inject the chosen variable
names before launching the environment. PostgreSQL aliases are `DATABASE_URL`,
`POSTGRES_URL` and `POSTGRESQL_URL`. Other engines require an existing URL.

The password is never returned to the browser or stored in project metadata.
The server retains only the reviewed provisioning plan for deployment retries and
automatic updates; CLI state retains resource identifiers. An available owned
database is reused on retry. Partially created owned network resources can be
reused after a failed attempt; unrelated resources are never adopted.

Schema creation/migrations are not automatic. Database setup failures stop the
deployment, hide credentials, and retain resource identifiers for inspection.
Failed builds, deployments and application deletion do **not** automatically
delete persistent databases. Use the existing database status/delete/snapshot
tools, review retained backups/network resources, and confirm charges separately.

## CLI

Use an operator-reviewed plan with `variables` added to the JSON above:

```json
{
  "clusterName": "YOUR_EXISTING_BEANSTALK_EKS_CLUSTER",
  "subnetIds": ["subnet-REPLACE_A", "subnet-REPLACE_B"],
  "variables": ["POSTGRES_URL"],
  "storageGb": 20
}
```

```sh
ebkit deploy https://github.com/OWNER/REPO --database-file database.json
```

Review the additional charges before confirming. `--no-wait` is incompatible with
database creation. To repeat this behavior directly through the CLI, pass the same
reviewed file on retries; the hosted worker reuses its saved plan automatically.

For an already deployed app, `operations database-attach --file settings.json`
accepts `{"variable":"POSTGRES_URL"}`; the default remains `DATABASE_URL`.

References: [RDS managed passwords](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_CreateDBInstance.html)
and [Beanstalk cluster networking](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/beanstalk-cluster-concepts.html).
