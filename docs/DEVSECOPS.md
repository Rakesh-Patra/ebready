# EBKit Git and application security

These controls extend the [deployment walkthrough](DEVOPS-HACKATHON-GUIDE.md).
Source/configuration changes here must pass CI before deployment. Repository
settings and local hook installation are separate actions; committing their files
does not enable them in GitHub or on another developer's laptop.

## Keep private material out of Git and Docker

`.gitignore` excludes local environment files, private keys, AWS profiles,
Terraform state/variables/plans and generated artifacts. `.dockerignore` excludes
the same sensitive inputs from the build context. The root Dockerfile also uses
specific COPY paths. Never solve a scan failure by committing a real key as a
test input. Use synthetic values assembled inside tests.

Ignored files can still be force-added. The staged-path check rejects private
configuration even in that case. It prints paths only, never file contents.
Example environment files and Terraform variable examples remain permitted.

Already committed credentials require revocation/rotation. Deleting a file or
adding an ignore rule does not invalidate a credential or remove older commits.

## Choose one local commit-hook installation

Native Git hook, using Python and Gitleaks 8.30.1 on PATH:

```bash
git config --local core.hooksPath .githooks
```

The tracked `.githooks/pre-commit` first checks staged paths, then runs Gitleaks
against the staged snapshot with redaction. Missing Gitleaks blocks the commit.
It does not scan unstaged developer configuration or print secret values.

Alternatively, use the pre-commit framework:

```bash
python -m pip install pre-commit==4.7.0
git config --local --unset core.hooksPath
pre-commit install
pre-commit run --all-files
```

Choose this alternative only when replacing the native hook configuration.
The Gitleaks hook revision is pinned to the reviewed release commit. Review
`pre-commit autoupdate` changes before committing them. Local hooks can be bypassed;
the full-history CI scan and protected branch are the server-side enforcement.

## Secret scanning and historical test data

```bash
gitleaks git . --redact --config .gitleaks.toml
```

CI checks out complete history, verifies the pinned Gitleaks release archive's
SHA256 digest, and scans the repository history. It keeps a redacted JSON report.
The configuration extends all upstream default rules.

One reviewed historical sanitizer test used a numeric-only synthetic PAT-shaped
input. `.gitleaksignore` excludes only its exact commit/path/rule/line fingerprint.
No test directory, credential rule or whole commit is broadly excluded.

GitHub secret scanning and push protection provide another layer when enabled in
repository settings. They complement Gitleaks; their provider/rule coverage differs.

## Source, dependency and infrastructure gates

- SAST: Semgrep checks reviewed local rules for dynamic execution, shell commands,
  disabled TLS verification, unsafe YAML and pickle use in application/tool code.
  Rules stay in the repository, with metrics disabled and no registry ruleset or
  source upload. This small ruleset is not comprehensive taint analysis.
- SCA: `npm audit --omit=dev --audit-level=high` checks shipped JavaScript packages.
  `pip-audit --strict` checks the exact third-party Python versions extracted from
  the built runtime image with standard-library package metadata. The local EBKit
  distribution is covered by source checks, rather than a public package lookup.
  Development/scanner dependencies stay outside that runtime inventory. Trivy
  additionally scans the actual runtime image.
- IaC: Checkov scans Terraform, the root Dockerfile and GitHub Actions. Findings
  fail the security job. Any accepted exception must be narrow and explain its
  deployment-specific reason next to the resource; no blanket soft-fail is used.
- Container and source scanning: Trivy gates fixed HIGH/CRITICAL vulnerabilities
  and secret findings. Its `ignore-unfixed` policy is a coverage limit, not evidence
  that an unfixed vulnerability is safe. The image SBOM records dependencies.

Pinned scanner dependencies are under `security/`; Dependabot reviews them weekly,
alongside app dependencies, the digest-pinned Docker base and Actions. Updating a tool can change findings and
must receive the same review as application code.

## Passive DAST and runtime isolation

CI runs the already scanned image in a disposable Docker network with demo mode
enabled. The app has no AWS credentials, no host port publication, and no mounted
developer files. The test uses a read-only root filesystem, writable temporary/job
mounts, dropped capabilities, no new privileges, and CPU/memory/PID limits.

A digest-pinned official OWASP ZAP container spiders that local target and performs
passive checks. Missing anti-clickjacking or content-type protections fail the
gate. Other warnings remain in the JSON/HTML report for review; `-I` permits WARN
results but does not permit FAIL findings or scanner errors. This is passive DAST,
not an active attack scan or a test of authenticated/cloud workflows.

The server sends anti-framing, nosniff, referrer and permissions headers. CSP is
still a reported warning: the application injects runtime configuration inline,
so a strict policy needs nonce/hash support and integration testing. The isolated CI target uses HTTP, so HSTS there does not establish HTTPS.
The Cluster deployment independently verifies its managed HTTPS endpoint.

The local baseline completed with 63 passing checks, no FAIL findings and four
warning categories: missing CSP, non-storable responses, SPA coverage advice, and
missing cross-origin embedder policy. These are retained for review. Cache policy
and cross-origin isolation need compatibility testing; the SPA warning also means
this traditional spider is not complete coverage of the React dashboard.

These Docker runtime flags are exercised in CI. The Cluster pod runtime is
service-managed and requires separate platform-specific hardening validation.
The live container runs nonroot and uses a multistage build. Distroless is not a drop-in runtime here: the app includes Python CLI
tools, Git and Node, rather than a Node-only static service.

## Branch protection, ownership and RBAC

`security/main-protection.json` is the reviewable desired policy for `main`:
required PR, current `verify` and `security` checks, dismissal of stale
approvals when reviews are used,
resolved conversations, linear history, and no force pushes or branch deletion.
Administrators are included in enforcement. At the owner's request this is a
solo-maintainer policy: the required independent approval count is zero, and
code-owner/last-push approval are not mandatory. When collaborators join, set the
count to at least one and enable code-owner and latest-push approval.

```bash
python scripts/harden_github.py          # preview
python scripts/harden_github.py --apply  # change settings and save a read-back receipt
```

`.github/CODEOWNERS` initially names the real repository owner, including sensitive
CI, Terraform, Docker, auth and security paths. Code-owner rules must exist on the
PR's base branch to take effect. An author cannot approve their own PR; add a trusted
independent reviewer before expecting an owner-authored PR to merge.

Give administrators settings access only when needed, maintainers merge/release
responsibility, developers PR access, and auditors read access. Teams/custom roles
depend on the account plan; do not pretend a personal repository has organization
teams. Require MFA for maintainers, remove stale collaborators, review installed
GitHub Apps, and keep production environment/deployment branch restrictions.

Actions start with `contents: read`. Only the protected deployment job gets
`id-token: write`; PRs never receive AWS credentials. Deploy waits for both security
and verification jobs and publishes the same scanned image by immutable digest.

## AI scanner integration from the supplied example

The pasted Sim.ai workflow is not enabled. First confirm ownership of that workflow,
what repository/PR data it retains, and who can access the result. Keep its API key
in a protected CI secret store and never interpolate PR titles/bodies directly into
shell source. Build a JSON payload from `GITHUB_EVENT_PATH` with a JSON parser.
Use bounded timeouts, `curl --fail-with-body`, and validate the returned scan status;
a successful HTTP trigger is not a completed security assessment. Fork PRs must not
receive integration credentials. A reviewed GitHub App/webhook can avoid executing
third-party integration credentials in untrusted PR code.

## Evidence and remaining work

Keep redacted Gitleaks, Semgrep, Checkov, dependency, Trivy, ZAP and SBOM artifacts
from the same source commit. Read the actual job conclusions before claiming a gate
passed. The walkthrough records live deployment evidence separately from prepared
configuration. A reviewed CSP, active/authenticated DAST, alarm notifications,
and independent review remain distinct follow-up work until verified.

References: [Gitleaks](https://github.com/gitleaks/gitleaks),
[Semgrep CLI](https://docs.semgrep.dev/cli-reference),
[Checkov CLI](https://www.checkov.io/2.Basics/CLI%20Command%20Reference.html),
[ZAP baseline](https://www.zaproxy.org/docs/docker/baseline-scan/),
[GitHub branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
