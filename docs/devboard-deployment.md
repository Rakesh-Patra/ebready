# Deploying Devboard

This guide is specific to the Devboard application. For general EBKit installation
and deployment instructions, see the [README](../README.md).

## Configure application settings

Create or edit `devboard/.env` locally with the values supplied by your database
provider:

```dotenv
POSTGRES_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require
PORT=8080
```

Replace every placeholder with the real connection details. Use a database endpoint
reachable from AWS and the TLS settings required by your provider. Keep `.env`
gitignored; do not commit or share real connection strings. Commit `.env.example`
with the expected variable names and blank values.

EBKit sends these settings to the Elastic Beanstalk application environment. It does
not create the database, configure network access, initialize schemas, or run
application-specific migrations. Make sure the database accepts connections from
the deployed application and run any required migrations separately.

## Deploy

Run the command from the parent folder containing the `devboard` checkout, or provide
an absolute path to the environment file:

```powershell
ebkit deploy https://github.com/Rakesh-Patra/devboard.git --app devboard --environment devboard-cluster --port 8080 --env-file ./devboard/.env
```

Alternatively, provide the connection value directly (avoid shared or recorded
terminals because command-line arguments may be visible in shell history or process
details):

```powershell
ebkit deploy https://github.com/Rakesh-Patra/devboard.git --app devboard --environment devboard-cluster --port 8080 --env "POSTGRES_URL=YOUR_CONNECTION_URL"
```

Repeat `--env KEY=VALUE` for additional settings. `--env-file` and `--env` can be
combined; command-line values override matching keys from the file.
