# Dashboard deployment practice — October 17, 2026

Goal: deploy the EBKit React dashboard and Node API to a standard AWS Elastic
Beanstalk Docker environment, verify it, then practice a version update and rollback.
This is separate from EBKit's Cluster Mode deployment engine.

## Prepare

From `web`, run `npm.cmd run build` and
`npm.cmd test -- --pool=threads --maxWorkers=1`. From the repository root run
`python scripts/package_dashboard.py`. The bundle is written to
`artifacts/ebkit-dashboard.zip` using an explicit source-file allowlist; local
environment files, credentials, installed dependencies and job state are excluded.

The root multi-stage Dockerfile builds Vite assets and installs the Node worker
and Python CLI. A non-root process serves the frontend, `/api` and `/insforge` on
`0.0.0.0:8080`. `/api/health` is the health endpoint. Runtime public InsForge
configuration is inserted into the page; the server admin key is never included.
Local development still defaults to a separate Vite server and loopback worker.

## AWS practice settings

- Profile: `default`; region: `us-east-2` (verify the account with STS).
- Platform: Docker on Amazon Linux 2023; single-instance practice environment.
- Instance profile: `aws-elasticbeanstalk-ec2-role`.
- Set `INSFORGE_URL` and `INSFORGE_ANON_KEY` using the existing project's public
  configuration, and `EBKIT_WEB_ORIGINS` to the exact dashboard origin.
- Keep `EBKIT_LIVE_ENABLED=false` and `EBKIT_GITHUB_AUTO_DEPLOY=false`.
  Do not copy local AWS keys or `INSFORGE_API_KEY` into this practice environment.
- `HOST`, `PORT`, `EBKIT_STATIC_ROOT` and `EBKIT_PYTHON` already have container defaults.

The container is intentionally for dashboard hosting practice with cloud controls
disabled. Enabling hosted deployment jobs later requires a scoped workload role,
private persistent jobs storage, reviewed quotas and recovery across instance
replacement. Container-local jobs are lost when the container is replaced.

An HTTP Beanstalk URL is sufficient for an initial page/health smoke check.
Before using real accounts publicly, configure a domain, HTTPS and the exact
InsForge authentication redirect origin. Secure auth cookies require HTTPS.
Docker and Scout are not bundled in this runtime; cloud preparation/security
scanning needs separate operator tooling before enabling those features.

## Evidence and daily update

1. Record the application version and environment's Ready/Green status.
2. Confirm the dashboard, a built JS asset and `/api/health` return HTTP 200.
3. Confirm `liveEnabled` is false and unauthenticated cloud actions are rejected.
4. Make a small visible change, rebuild/package, deploy a new version, and verify
   the same environment URL.
5. Roll back to the previous version and check the page and health again.
6. Save screenshots/log summaries without credentials. Record any check that
   could not be completed rather than marking it passed.
7. Review and commit the deployment files and today's meaningful application work,
   then push to GitHub. No old commit dates need to be changed.
8. Post a LinkedIn recap using actual outcomes: container packaging, deployment,
   health checks, troubleshooting and rollback. Include a screenshot and repo link.

AWS resources accrue charges while running. After practice, terminate the named
practice environment and verify termination. Application versions, source bundles,
logs and the empty EB application may require separate reviewed cleanup.

References: [AWS Docker quick start](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/docker-quickstart.html)
and [Docker image/source format](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/single-container-docker-configuration.html).
