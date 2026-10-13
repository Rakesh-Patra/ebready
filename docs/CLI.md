# EBKit CLI

Install with `python -m pip install -e ".[google,database]"`. Python 3.11+ and AWS credentials are required. Docker is needed for local container verification.

## Deployment and GitHub automation

```sh
ebkit init --path .
ebkit doctor --path . --production --pin-bases --verify --scout
ebkit deploy https://github.com/owner/repo --yes
ebkit watch https://github.com/owner/repo --yes
```

Watch checks every 300 seconds, deploys a pinned commit once, preserves environment settings and never recreates a destroyed environment. ZIP updates remain manual. Keep the watcher running. CLI deployments use your own AWS account and have no hosted trial deadline.

Use `--app`, `--environment`, and `--region` to select targets. Named credentials profiles use `ebkit --profile PROFILE COMMAND`.

## Environment controls

`status`, `logs`, `events --follow`, `health --refresh`, `versions`, `resources`, `config`, `options`, `scale`, `clone`, `swap`, `rollback`, `version-delete`, `destroy`, and `cleanup-status` provide deployment lifecycle controls. Run each command with `--help` for required parameters. Configuration values are hidden. AWS saved configuration templates (`config-save`/`config-apply`) support Standard environments only.

## Production integrations

```sh
ebkit operations metrics --app APP --environment ENV
ebkit operations autoscale --app APP --environment ENV --file policy.json --yes
ebkit operations cost --app APP --environment ENV
ebkit operations pipeline --app APP --environment ENV --file pipeline.json
```

See [production operations](OPERATIONS.md) for JSON examples and external-service setup. These commands call real AWS APIs; automated tests mock AWS and do not create billable infrastructure.
