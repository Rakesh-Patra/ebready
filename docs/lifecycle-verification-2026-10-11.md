# EBKit lifecycle verification — October 11, 2026

Target: tws-labs, application `ebk-48a621e65a544df6`, region `us-east-2`.

## Passed against AWS

- Existing deployment reported Ready / Green; HTTPS returned HTTP 200.
- CLI status, versions and resources returned the expected environment.
- Logs returned build/application sections without permission errors. Contents were kept private.
- Scaling accepted the existing minimum/maximum bounds and remained healthy. Replica growth and load-driven automatic scaling were not tested.
- A nonexistent rollback version was rejected before updating the environment.
- Redeploy built a new image/version, reached Ready / Green and preserved the hostname and environment settings. Settings were compared using a hash; values were not printed.
- Rollback restored the original version and returned Ready / Green with the same hostname/settings.
- Authorized destroy terminated the environment. The empty EB application, ECR repository, CodeBuild project and build log group were verified removed. No target environment log streams remained in the shared groups.
- The website record was updated to destroyed with its live URL cleared.
- Restarted backend health returned HTTP 200; an invalid session was rejected with HTTP 401. Restarting did not submit a deployment.

## Automated verification

56 website/worker tests and 56 Python lifecycle tests passed with mocked AWS operations. Frontend production build passed. These cover repository failures, authorization, ownership, supplied-secret redaction, command validation and lifecycle error handling. One initial UI timeout passed on rerun with a longer timeout.

## Fixes made

Status now has structured JSON output; the worker saves the reported status, health and URL rather than updating log text only. Latest-build log lookup no longer sends unsupported `maxResults` to CodeBuild; a botocore Stubber regression test verifies the request.

## Remaining limitations

This was a CLI/AWS integration test plus automated website/API tests; no real browser click-through was available. Missing Dockerfile/build-failure cases were mocked, not intentionally deployed. Arbitrary application-emitted secrets cannot be guaranteed absent; applications must avoid printing secrets.

The worker still lacks durable recovery for interrupted jobs. A backend crash can leave saved operation flags stale. Restart recovery must be implemented before unattended trials. Automatic scaling under load has not been verified.

Managed cluster stack was still CREATE_COMPLETE after environment/artifact cleanup. Elastic Beanstalk schedules deletion three hours after its last environment terminates; reuse cancels pending deletion. Charges may continue until deletion finishes. Recheck with the recorded state:

```powershell
$env:EBKIT_STATE_FILE = Join-Path (Get-Location) 'web/server/.jobs/48a621e6-5a54-4df6-a2a4-8102958e0013.json'
.\.venv\Scripts\python.exe -m ebkit.cli cleanup-status --app ebk-48a621e65a544df6 --environment ebk-48a621e65a544df6-cluster --region us-east-2
```

AWS guidance: https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/beanstalk-cluster-concepts.html
