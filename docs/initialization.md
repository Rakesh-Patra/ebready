# Initializing an application

`ebkit init` generates or validates deployment files, including a root-level
`Dockerfile`. It does not deploy the application or provision its database.

## Choose a project

Run init in a local project:

```powershell
ebkit init --path .
```

Or initialize a public GitHub repository:

```powershell
ebkit init --repo https://github.com/OWNER/REPOSITORY
```

The GitHub repository is cloned into a folder named after the project in the current
directory. Init stops rather than overwriting an existing clone directory.

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

Review generated output and never commit or share the API key. For multi-tier
projects, init may use Docker AI (Gordon) to generate a Dockerfile and falls back to
Gemini if needed. Generation is best-effort: inspect the Dockerfile and run the app
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
