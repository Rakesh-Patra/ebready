# EBKit web application

The React dashboard uses **InsForge for accounts and project storage**, with Google OpenID Connect and email/password authentication. A Node backend validates public GitHub repositories and runs the Python EBKit CLI for live AWS deployments and management.

## Run locally

Use Node.js 22.12+ and Python 3.11+. From the repository root, install EBKit with `py -m pip install .`, then run `npm.cmd ci` inside `web`.

Start two terminals in `web`:

```powershell
# Terminal 1
npm.cmd run backend

# Terminal 2
npm.cmd run dev
```

Open **http://127.0.0.1:5173/**. Vite proxies `/api` to the local backend on port 8787. Startup checks saved job records and InsForge; it does not launch AWS commands. Previously authorized jobs can keep running during a backend restart.

## Accounts

**Create account** registers an email/password account. Enter the emailed verification code to open the account. **Log in** accepts an existing account. **Continue with Google** uses OpenID Connect through InsForge's PKCE flow. **Forgot password** requests an email code, exchanges it for a reset token, then saves the new password.

The current branch uses code-based verification and password reset; keep those methods for these forms. Google redirects to the exact frontend origin. `web/insforge.toml` records the local callbacks. Add the exact HTTPS origin before public hosting.

## Configuration

The gitignored `web/.env` contains only public `VITE_INSFORGE_URL` and `VITE_INSFORGE_ANON_KEY`. Never put an admin key, AWS key or application secret in a `VITE_` variable; it becomes part of the browser bundle.

The gitignored `web/server/.env` is configured for the linked `ebkit-web` branch. On another machine copy `server/.env.example` to `server/.env` and set:

```dotenv
INSFORGE_URL=
INSFORGE_ANON_KEY=
INSFORGE_API_KEY=
EBKIT_PYTHON=
EBKIT_LIVE_ENABLED=false
EBKIT_ALLOWED_EMAILS=
EBKIT_WEB_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
GEMINI_API_KEY=
EBKIT_GEMINI_MODEL=gemini-2.5-flash
```

`INSFORGE_API_KEY` is the server-only project admin key. `EBKIT_PYTHON` is the full path to Python with EBKit installed. Set `EBKIT_LIVE_ENABLED=true` and list approved account emails in `EBKIT_ALLOWED_EMAILS`, separated by commas. This workspace allows the current InsForge operator's email. Restart the backend after changing its configuration.

The worker uses its standard AWS credential chain: configured AWS profile, process environment, or IAM role when hosted. **Users never provide AWS credentials through the website.** The worker's AWS identity pays for live resources. Use a dedicated, scoped role. Users deploying into their own AWS account should use EBKit from their terminal.

The worker binds only to loopback. Do not expose the development Vite server publicly.

## Deploy and manage

1. Log in using an operator-approved account.
2. Click **New deployment**, choose **GitHub link** or **Upload ZIP**. ZIP mode requires login and operator approval even for preparation; it makes no GitHub API requests.
3. **Check repository & continue** checks GitHub metadata and the root `Dockerfile` on the default branch. Missing/private repositories, missing Dockerfiles, rate limits and connectivity errors appear inline. This verifies presence, not build correctness or CVEs.
4. Enter a project name, region, port and optional `KEY=VALUE` settings. Importing `.env` reads the file locally. In live mode, values are sent only on deployment, passed through a temporary server file to EBKit, then removed after the operation. The project database stores variable names only. Known supplied values are redacted from worker output. Applications should never log secrets themselves.
5. Confirm AWS charges and click **Deploy to AWS**. EBKit uses CodeBuild, ECR and Elastic Beanstalk Cluster Mode. Logs contain actual CLI output. Ready requires the saved AWS deployment result to report Ready and Green; failures remain failures.

### ZIP preparation and review

Upload a ZIP up to 25 MB, then choose **Detect project settings**. Review the detected container port, correct it if needed, and check **I checked the container port** before preparing the Dockerfile. Detection is a suggestion; it does not change the application listening port. A single enclosing folder (including GitHub's downloaded ZIP layout) is flattened. Unsafe paths, links, duplicate paths, excessive compression, more than 5,000 entries or more than 200 MB expanded are rejected. `.env` files, common credential files and installed dependencies are omitted; keep secrets out of all source files yourself.

The existing root Dockerfile is displayed. If missing, approve AI analysis and **Prepare & review Dockerfile** first uses the existing Docker AI (Gordon) integration. If Gordon is unavailable, fails or returns an invalid draft, Gemini uses the operator's server-side `GEMINI_API_KEY`. Only project scan metadata and selected manifests are supplied to generation; Gordon is instructed to return text without reading files, modifying files or running commands. The review screen identifies the provider. The key is never returned to the browser, put in a `VITE_` variable or passed to a deployment subprocess. Each approved account has five AI attempts per UTC day; missing/limited Gemini access produces a visible error and cannot enable deployment.

Review the full Dockerfile, then check **I reviewed this root Dockerfile**. Download the Dockerfile or the prepared ZIP, or continue to configuration. The backend independently verifies ownership and the reviewed Dockerfile hash before deployment. Enter real environment values separately; archive preparation generates names/placeholders without provisioning a database. Preparation then runs the same Docker Scout source-dependency validator as local init, without building an image. The source gate requires zero Critical CVEs and at most five High CVEs. Failures or unavailable Scout block continuation and are independently rejected by the deployment backend. The Dockerfile and full findings remain available for review/download so dependencies can be fixed. This does not scan the application image or certify runtime/build correctness.

The website also preserves an explicit Scout bypass: the user must acknowledge that security will NOT be verified, then run **Prepare with Scout bypass**. The warning remains visible during review and deployment configuration. A missing/failed scan never silently becomes a pass.

ZIP builds use private encrypted S3 source storage and CodeBuild, with object-specific read permissions for the build role. Source objects are removed after the build; a one-day lifecycle rule covers abandoned sources. Destroy removes the verified application-owned source bucket and retained local ZIP source. Unclaimed uploads expire after 24 hours and are purged on a later upload. Claimed source is retained on private persistent disk for redeploy/recovery; redeploy builds the same reviewed uploaded snapshot. Choose **Upload ZIP & redeploy** on an existing ZIP project to prepare and review a replacement ZIP. It updates the same application/environment and keeps the URL; provide required environment values again. Redeploy without a replacement through the API reuses the existing snapshot. GitHub redeploy builds the repository latest code. Auto-deploy on GitHub push is planned and will apply only to GitHub projects; ZIP updates are manual.

For hosted CLI ZIP init, sign in on the website and use **Deploy with CLI → Copy CLI session token**. Configure `EBKIT_SERVICE_URL` to the website origin and `EBKIT_ACCESS_TOKEN` to that session token. Run `ebkit init --zip project.zip`; the service tries Gordon first, falls back to the operator Gemini key, prints the Dockerfile for confirmation and downloads the prepared source. Keep the token private; it expires with the account session. Hosted init runs source Scout by default and does not run local builds. `--no-scout` requires an explicit bypass and prints SECURITY NOT VERIFIED; it does not fix vulnerabilities.

Project controls:

- **Redeploy in AWS** starts a real build/deployment. Existing environment settings are preserved when no replacement values are supplied.
- **Status / Logs / Versions / Resources / Cleanup-status** run the corresponding EBKit command and show its output in Logs.
- **Apply AWS scaling** changes replica bounds from 1 to 100 and affects compute charges. Cluster Mode cannot scale to zero.
- **Rollback in AWS** requires an existing version label. Load Versions in Logs, enter its exact label in Settings, then invoke rollback from Overview.
- **Destroy** requires the generated environment name. It terminates the environment and cleans eligible EBKit artifacts while retaining cleanup metadata. Shared infrastructure and external services remain. Managed EKS deletion can stay pending and continue charging after environment termination; inspect Cleanup-status.

The pilot worker runs one operation at a time. Each user can have three active live projects, with five launches per worker process. Keep the operator allowlist; durable quotas and billing controls are needed before open public paid deployments.

### Interrupted-job recovery

Cloud commands run in a detached runner that writes atomic job records and redacted logs to the private `server/.jobs` directory. Restarting the HTTP backend reconnects to the existing runner without starting another build, deployment, rollback or destroy command. The original two-hour operation timeout remains in effect across backend restarts; this is a command timeout, not the planned trial expiry feature.

If a job completes while the backend is offline, its result is applied when the backend returns. Failed database updates are retried from the saved completion record, and revision updates are idempotent. A second backend cannot use the same directory concurrently.

If the runner and CLI were also stopped, recovery clears the stuck running flag and shows an **Operation interrupted** error. Check **Status** and **Resources**, then retry or destroy as appropriate. Recovery never automatically repeats cloud mutations or claims that interrupted cleanup succeeded. If the runner died but its CLI process is still alive, the worker holds the operation lock until that process exits, preventing duplicate actions.

Use the same private persistent disk across restarts. Optionally set `EBKIT_JOBS_DIR` to its absolute path. Journals contain command metadata and redacted output; supplied secret values remain only in the temporary `.env`, which is removed on completion. A host shutdown removes the running processes; it cannot resume a CLI mid-command. Missing legacy job records are reported as interrupted. Startup fails closed if the database or job records cannot be checked.

For a missing Dockerfile, run `ebkit init` locally, review the generated Dockerfile and Scout findings, then push it. The existing CLI Scout bypass and warning remain available. The web presence check does not run Scout.

## Demo mode

**Switch to demo** enables controls without AWS actions. GitHub validation still runs. Build/security/health stages are explicitly simulated. Guest demo projects disappear on refresh; signed-in projects persist and simulated progress resumes after reload. Demo `.env` import keeps names and discards values.

## Database security

Website changes are isolated in the `ebkit-web` schema-only branch. Apply both root migrations when setting up another intended project:

- `20261011060931_ebkit-web-projects.sql`
- `20261011062758_ebkit-web-live-worker.sql`
- `20261012101600_ebkit-web-zip-sources.sql`

Owners can read their projects through row-level security. Browser clients may create/update/delete only their own demo records. The authenticated worker writes live AWS records and results after checking ownership and the operator allowlist. Clients cannot change ownership or live status.

## Verify

```powershell
npm.cmd test -- --pool=threads --maxWorkers=1
npm.cmd run build
npm.cmd audit
```

Tests cover account flows, wizard clicks, GitHub checks, command construction, ownership, allowlist enforcement, secret redaction and worker execution using mocked AWS subprocesses. Recovery tests use a real detached Node runner with a harmless CLI substitute to verify restart adoption, database retry, orphan detection and duplicate-operation prevention. These checks do not create billable AWS resources. User sign-in and AWS role permissions still require end-to-end verification; unit tests do not prove them.

## Public hosting

Serve `dist/` and proxy `/api` to the worker over HTTPS. Configure that origin in InsForge and `EBKIT_WEB_ORIGINS`, provide server-only secrets, and use a dedicated IAM role. Run the worker under a process supervisor with a private persistent `.jobs` volume for deployment state and recovery records. Configure the supervisor to restart the HTTP backend independently of detached job runners. Killing an entire container or process group also kills its jobs; recovery reports those as interrupted.

Before broader production use, replace this single-host execution model with a durable distributed queue, shared quotas, encrypted secret storage and rate limiting. This recovery implementation supports one backend host and persistent disk; it does not coordinate multiple replicas or hosts.

### Required environment settings

Repository validation reads public example environment templates and up to 40 small source files (64 KB each) without executing code. It detects common runtime environment accesses in several languages, omits EBKit-managed PORT/HOST settings, and returns names only. Required names are inserted with blank values; enter connection values or import a local `.env`. The deploy button stays hidden while required values are missing, and the backend independently rejects the initial deployment before creating project/AWS resources.

Detection is heuristic. Blank template settings and common database/cache connection references are treated as required; optional template comments and defaults are respected. Where runtime references are available, unused template entries (such as Compose-only settings) are omitted. Review repository documentation for dynamic settings or dependencies the scan cannot identify. This check does not provision or test the database connection. Existing deployments retain settings during manual redeploy.
