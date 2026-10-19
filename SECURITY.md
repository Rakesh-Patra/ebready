# Security reporting and supported deployment

Report a vulnerability through this repository's private GitHub security reporting
channel when available. If it is unavailable, contact the repository owner to
arrange a private channel. Do not put credentials, customer data, exploit tokens,
or private logs in a public issue or pull request.

The public practice deployment is a guest simulation. Authentication, customer
AWS work and hosted AI operations remain disabled while HTTPS is unavailable.
Do not enter credentials into the public demo.

The maintained source is `main` plus the active deployment feature branch.
Scanner reports describe the rules and dependency versions checked at a point in
time; they do not establish that the application is free of vulnerabilities.

For a suspected credential exposure: revoke/rotate the credential first, assess
its use in provider audit logs, then remove it from the current source and plan a
coordinated history cleanup if necessary. `.gitignore` does not remove history.
Do not force-push shared history as an automatic remediation.

See [the DevSecOps controls](docs/DEVSECOPS.md) for local hooks, CI gates,
repository access, scanner coverage, and current limitations.
