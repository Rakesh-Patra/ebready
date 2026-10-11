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
