# Live CLI verification — 14 October 2026

Tests used the public [devboard repository](https://github.com/Rakesh-Patra/devboard) and isolated AWS resources in us-east-2. ZIP upload testing was excluded from this live run. Local evidence is retained under ignored `artifacts/aws-cli-live`; credentials and database URLs are excluded from Git.

## Completed checks

- All 24 top-level CLI help pages and the 15 production operations actions were reviewed. Local automated suites passed: Python **480 tests**; web **195 tests**, with a successful production build. A run outside the normal sandbox encountered Windows temporary-directory permissions; rerunning in the normal environment passed.
- Actual AWS deployment, status, health, logs, events, environment listing, configuration read/write/unset, generic options, application versions, resources and diagnosis.
- Replica scaling and CPU/memory autoscaling configuration; actual historical CloudWatch datapoints. An actual alarm delivered through SNS to an isolated SQS queue. A budget was created and retrieved; delivery of budget emails was not verified.
- Clone, CNAME swap and swap-back, manual rollback, unused version deletion and rejection of active version deletion.
- Standard environment configuration template save/apply returned successfully and HTTP health returned 200. Enhanced health continued to report a deployment-version warning; this is not an unconditional healthy result. Cluster mode correctly rejects unsupported templates.
- Private encrypted RDS creation, status, network grants, application attachment, real SQL connectivity through an SSH tunnel, migrations, repeat-migration no-op, changed-checksum rejection and snapshot creation.
- A failing application test prevented deployment without changing the running version, settings or URL. A successful deployment changed the version while retaining the settings and URL. Failed release verification automatically restored the previous version.
- GitHub watch detected actual commit `fb474a3d5136a6b0dda1898275582787444f99e6`, deployed its health marker, and kept the URL and settings. No-change polling did not redeploy. Revert `7caccd851e5f` was subsequently deployed successfully. Default polling is 300 seconds.
- GitHub Actions application tests and AWS OIDC authentication both passed, followed by a read-only CLI status check: [verification run](https://github.com/Rakesh-Patra/devboard/actions/runs/38568479492). This verifies OIDC and job ordering; full pipeline deployment and protected-environment reviewer approval were not exercised.
- Resource inventory now includes both database/network tags and the older ECR/CodeBuild application tags, without duplicate resources; actual AWS inventory returned the build project and image repository.

## Docker security scope

The original devboard Docker image built, but its scan reported **15 critical and 45 high vulnerabilities**. Its Vite/plugin compatibility check also failed. This original source is not certified production-ready.

A separate local copy received updated frontend and Go dependencies. Real Gemini analysis and Docker generation produced a non-root distroless runtime. Docker AI repaired inaccessible registry bases and a mismatched Go builder version. The linux/amd64 image then built, ran against the private test database and returned HTTP 200. The built-image Docker Scout gate passed with **zero critical and zero high vulnerabilities**. This result applies to the corrected copy, not the original GitHub dependency versions. The generator policy was also corrected to request public registry bases.

The subsequent `doctor --production --require-distroless --pin-bases --verify --scout` check passed static, build, runtime and security verification, with real registry digest pins. Its final image scan reported **0 critical, 0 high, 0 medium and 1 low vulnerability**. No database URL was embedded in the Dockerfile.

## Teardown

Deployment `destroy` is the final deployment operation. Database deletion and temporary resource cleanup are separate, because `destroy` intentionally retains external databases and shared IAM/infrastructure. Beanstalk may retain its managed EKS cluster for three hours after the last environment terminates, or reuse it for another environment. Cleanup is only complete when AWS confirms the resources are gone.

Final results: Standard, primary Cluster and cloned Cluster environments all reached **Terminated**. Both final CLI destroy calls returned exit code 0. The clone initially encountered an AWS certificate-in-use error after CNAME swapping. After primary termination, the certificate was verified as unused and tagged to the disposable clone, then removed; clone termination completed. No unrelated certificates were removed.

AWS confirmed removal of the test application, CodeBuild project and ECR repository. The test RDS instance, its manual/final snapshots and retained automated backups, database subnet/security groups, SSH runner/key pair/security group, alert topic/queue/subscription, budget, temporary CI branch, fixture IAM roles and source bucket were cleaned up. Local validation containers, network and tunnel were removed.

`cleanup-status` passed and accurately reported **managed EKS cleanup still pending** with its stack in `CREATE_COMPLETE`. Shared pre-existing roles and the OIDC provider were retained. Therefore this report does not claim all AWS charges have stopped. See [AWS cleanup guidance](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/beanstalk-cluster-getting-started.html).
