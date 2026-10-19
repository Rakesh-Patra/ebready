# Repository maintenance

Use `main` as the long-lived branch. Make changes in one short-lived pull-request
branch, require the `verify` and `security` checks, and delete that branch after
merging. A branch is a review workspace; it is not another deployed application.

## One CI workflow

`.github/workflows/delivery.yml` is the only committed workflow. Its security,
verification, and conditional deployment jobs are separate stages of one workflow.
Pull requests run checks; pushes to `main` verify the merged result. New commits
cancel superseded checks on the same PR. Push/manual deployment runs are not
cancelled mid-release. A feature branch no longer triggers both push and PR runs.

GitHub's built-in Dependabot Updates and Dependency Graph entries are dependency
maintenance services, not additional workflow files in this repository. Historical
failed runs remain available for auditing; current checks determine merge readiness.

## Dependency updates

Dependabot groups routine updates across npm, pip, Docker, and GitHub Actions into
one weekly maintenance PR. The root pip updater already finds the security-tool
requirements, so a second `/security` updater is unnecessary.

Routine major-version updates are deferred for explicit compatibility review.
This includes the proposed Node 26, TypeScript 7, jsdom 30, Lucide 1, and major
action upgrades. Keep security alerts enabled and review advisories promptly;
any security fix requiring a deferred major needs a reviewed migration. Security
updates can appear outside the routine version-update group.

The cleanup consolidates user-event 14.6.8, Vitest 5.0.5, and Semgrep 1.181.0.
React and React DOM remain pinned to the same tested version. Optional major
updates were not merged solely to remove failed badges. Their branch/PR metadata
and Git commits were backed up privately before branch removal.

## Deployment remains explicit

CI success does not require an AWS deployment. The deploy job requires the
configured `DEPLOYMENT_BRANCH` and OIDC role. While it is `overnight-paused`,
merges and CI checks do not restart the shut-down AWS environments.
