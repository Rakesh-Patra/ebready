# Initializing an application

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
tries Docker AI (Gordon) first, then Gemini if Gordon is unavailable or fails,
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
