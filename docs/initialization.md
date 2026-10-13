# Initializing an application

Dockerfile generation now uses reviewed distroless recipes where source metadata
is sufficient (Node 22/24/26, Python 3.13, static Go, and simple Java applications).
Other projects use Gordon/Gemini behind the same production quality gate. Invalid
drafts and invalid repairs are rejected; EBKit never guesses a base tag, inserts
`useradd` into a shellless runtime, or hides a build error with `|| true`.
Unsupported package managers/runtime combinations need a complete reviewed recipe;
an empty or incomplete generic template cannot pass the gate.

The gate checks multi-stage separation, the final non-root user, exec-form startup,
runtime/builder compatibility, ports, secrets, copy syntax and missing build inputs.
Node `npm ci` requires an actual lockfile copied before installation. Build tools
and development dependencies belong in builders. Distroless Python 3.13 uses a
matching Debian trixie builder; other Python versions retain a compatible minimal
runtime rather than being silently upgraded. No universal Dockerfile can fix
vulnerabilities in application dependencies without reviewing those dependencies.

After generating/reviewing the files, verify the actual image locally:

```powershell
ebkit doctor --path . --port 8080 --production --require-distroless --pin-bases --verify --scout
```

`--pin-bases` resolves real registry SHA256 digests and displays an approval diff;
it also refreshes existing tag-based pins. `--verify` builds with `--pull` and probes
HTTP, then cleans its temporary container/image. `--scout` scans that built image
with a zero Critical / zero High CVE threshold. Unavailable scans, unknown severity
and incomplete reports never pass. This is separate from the existing source Scout
gate. Static quality and a distroless tag alone do not prove build/runtime/CVE health.

The website displays a **Dockerfile quality report**, identifies reviewed recipes
versus Gordon/Gemini, and blocks generated files which fail the policy. Use **New
deployment → Upload ZIP** to prepare an archive, or enter a public GitHub URL and
choose **Prepare Dockerfile from GitHub snapshot**. Both routes prepare the same
root-Dockerfile snapshot; the GitHub repository itself is not changed. The generated
file can be reviewed/downloaded and committed manually, or the prepared snapshot can
be deployed as ZIP source. For detected multi-tier projects, one root Dockerfile is
prompted to build application tiers while keeping stateful services external.
Uploaded code is not run on the worker: image build/runtime/image CVEs remain
unverified until the reviewed source is downloaded and checked locally.

`scripts/verify_production.py` generates six small applications, pins real image
digests, builds/probes their containers, inspects their runtime filesystem and user,
and scans their images. `--live-gordon` additionally validates a real Gordon draft.
Results and Dockerfiles are retained under the chosen `--output`; it creates no AWS
resources and removes only its uniquely named validation containers/images.

`ebkit init` generates or validates deployment files, including a root-level
`Dockerfile`. It does not deploy the application or provision its database.

## Choose a project

Run init in a local project:

```powershell
ebkit init --path .
```

Or initialize a project ZIP:

```powershell
ebkit init --zip ./project.zip
```

ZIP init safely extracts source, generates deployment files and exports a prepared ZIP.
For hosted init using the operator Gemini key, configure `EBKIT_SERVICE_URL` and
`EBKIT_ACCESS_TOKEN` from an approved website session; no user AI key is needed.

GitHub links are deployment-only. Create and review the Dockerfile locally, push it
to GitHub, then run `ebkit deploy https://github.com/OWNER/REPOSITORY`.
The old `init --repo` option now returns this guidance rather than cloning/analyzing a URL.

## AI provider

Gemini is the active/default provider. Set an API key before starting the wizard:

```powershell
$env:GEMINI_API_KEY = "your-key"
ebkit init
```

On macOS/Linux:

```bash
export GEMINI_API_KEY="your-key"
ebkit init
```

Review generated output and never commit or share the API key. Dockerfile generation
tries reviewed recipes where supported, then Docker AI (Gordon), then Gemini if Gordon is unavailable or fails,
for both single-tier and multi-tier projects. Generation is best-effort: inspect the Dockerfile and run the app
locally to verify its behavior.

## Validation and local builds

Init statically validates generated files and runs Docker Scout against source
dependencies by default. This scan does not inspect a built image. Use `--no-scout`
to skip the scan; if Scout is unavailable, security validation is reported as
unverified. A failed security gate blocks readiness but does not remove generated
files. Update dependencies and lockfiles, then rerun init.

Local image verification is optional. Use `--build` to request a local build;
`--runtime` also builds and probes `/` and `/health`. Interactive init asks before a
build and defaults to skipping it. `--yes` skips building unless `--build` or
`--runtime` is explicitly supplied. `--scout` alone does not trigger a build.

## Stateful dependencies

Databases, Redis, and other stateful services are not bundled into the app container
or provisioned by init. Init can list detected services and add connection-variable
names (not values) to `.env.example`; it can create/update a local `.env` with blank
settings and ensure it is ignored by Git. Provision the services separately, configure
network access, then populate `.env` locally. Do not commit real environment files.
