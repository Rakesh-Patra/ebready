# Contributing to EBKit

Thanks for helping improve EBKit. Changes should keep the CLI safe, understandable,
and compatible with the supported Python versions.

## Set up a development environment

Python 3.11 or newer is required. From a clone of this repository:

```powershell
py -m pip install -e ".[dev]"
py -m pytest
```

On macOS or Linux, use `python3` instead of `py`.

## Before opening a pull request

- Run the tests relevant to your change; run `py -m pytest` for the full suite.
- Update README or command help when user-visible behavior or options change.
- Add or update tests for behavioral changes, especially AWS operations and cleanup.
- Never include AWS credentials, API keys, real `.env` files, or other secrets.
- Do not run tests or examples that create or delete live AWS resources unless the
  test is explicitly designed for it and you have authorization.

## Pull requests

Keep each change focused. Describe the user impact, notable behavior changes, and
the tests you ran. For changes that affect deployment or resource cleanup, clearly
state which AWS resources can be created, updated, or deleted.

## Verify a fresh clone on Windows

The web ZIP integration tests invoke Python. A fresh clone has no `.venv`, so explicitly select the interpreter when using a global installation:

```powershell
$env:EBKIT_TEST_PYTHON = (Get-Command python).Source
Set-Location web
npm.cmd ci --no-audit --no-fund
npm.cmd test
npm.cmd run build
```

Use `npm.cmd` when PowerShell blocks `npm.ps1`. On a slow machine, rerun timed-out tests with `npm.cmd test -- --maxWorkers=2 --testTimeout=20000` and report that setting with the results. If supplying pytest `--basetemp`, create its parent directory first.

The [command reference](docs/COMMANDS.md) lists the 24 CLI commands and the live verification checklist. Generate six Dockerfile samples without Docker using `python scripts/verify_production.py --output artifacts/production-check --generate-only`. Omit `--generate-only` to build, run and scan the images; static checks alone do not establish image security or runtime health.
