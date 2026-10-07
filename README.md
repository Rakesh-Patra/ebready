# EBReady

EBReady is a platform designed to simplify deploying applications to AWS Elastic Beanstalk Cluster Mode environments.

---

## Day 1 — Deployed FastAPI Hello World ✅

Deployed a minimal FastAPI application to an existing AWS Elastic Beanstalk Cluster Mode (Amazon EKS) environment.

- **Region:** `us-east-2`
- **Application:** `ebready`
- **Environment:** `ebready-dev`
- **Live URL:** `http://ebready-dev.eba-unb4v5ht.us-east-2.elasticbeanstalk.com`

---

## Day 2 — AI-Powered Deployment Kit Generator ✅

EBKit is the AI-powered deployment-kit generator built inside EBReady.

---

## Architecture

```text
Repository
    │
    ▼
┌─────────────────────┐
│  ProjectScanner      │  Heuristic file inspection — no AI
│  (scanner.py)        │  Detects: language, framework, port,
└──────────┬──────────┘  entrypoints, dep files, existing artefacts
           │
           ▼  ScanResult (structured dict)
┌─────────────────────┐
│  Gemini Analyzer     │  Analyzes project metadata & requirements
│  (ai_analyzer.py)    │  Returns ONLY structured JSON
└──────────┬──────────┘
           │
           ▼  Raw JSON
┌─────────────────────┐
│  Pydantic Validation │  Strict schema — rejects hallucinations,
│  (DeploymentConfig)  │  shell injection, secret values, bad enums
└──────────┬──────────┘
           │
           ▼  Validated DeploymentConfig
┌─────────────────────────────────────────────────────────────┐
│  Generation Layer                                           │
│  ├── Docker AI (Gordon): Generates production Dockerfile    │
│  └── Jinja2 Renderer: .dockerignore, Procfile, .ebignore,   │
│                       .env.example                          │
└──────────┬──────────────────────────────────────────────────┘
           │
           ▼  Deployment Kit (Cluster Mode)
┌─────────────────────────────────────────────────────────────┐
│  EBReady Multi-Stage Validation & Security Gate             │
│  ├── Cross-File & Secret Validation                         │
│  ├── Docker Build Validation                                │
│  ├── Container Runtime Health Check (optional)              │
│  ├── Docker Scout Vulnerability Scan (0 Critical CVEs)      │
│  └── Cluster Mode Preflight Gate                            │
└──────────┬──────────────────────────────────────────────────┘
           │
    Failure Detected?
    ├── YES ──► 🔧 Docker AI (Gordon) Diagnoses & Repairs Dockerfile
    │           └── EBReady re-validates & rebuilds (2–3 attempts max)
    └── NO  ──► ✅ PROJECT READY FOR DEPLOYMENT
```

### Separation of Responsibilities

- **Gemini** — Analyzes repository metadata, framework, runtime version, entrypoint, and ports to produce a strictly validated `DeploymentConfig`.
- **Docker AI (Gordon)** — Uses the developer's installed Docker AI agent to generate the production-ready `Dockerfile` and automatically diagnose/repair container build, runtime, and CVE issues.
- **EBReady** — Validates all artifacts before rebuilding, protects secrets and `.env`, enforces that application source code is never modified, runs the Docker Scout security gate, and evaluates Cluster Mode preflight compatibility.

### Why This Architecture?

**AI does NOT write deployment files directly.**

The AI only fills in a structured `DeploymentConfig` JSON object.  Pydantic
validates every field with strict rules before any file is generated.  Jinja2
templates then render the actual deployment files.  This means:

- AI hallucinations cannot produce malformed Dockerfiles
- Shell injection via `start_command` is explicitly rejected
- Secret values can never leak into `.env.example`
- Templates are version-controlled and auditable
- Swapping AI backends requires zero template changes

---

## How AI Is Used

The AI receives the **scan result** (structured metadata about the repository) and is instructed to:

- Return ONLY structured JSON matching `DeploymentConfig`
- Prefer evidence from the scanned repository
- Mark uncertain fields explicitly rather than inventing values
- Never generate file content directly

The system prompt:

> "You are a deployment configuration analyzer. Analyze the supplied project
> metadata and determine the safest deployment configuration. Return ONLY
> structured JSON. Do not generate Dockerfiles. Do not generate shell commands.
> Do not invent dependencies. If information is uncertain, explicitly mark it."

---

## Why Jinja2?

- **Separation of concerns** — templates are separate from logic
- **Auditable** — every generated file has a traceable template source
- **Multi-language** — same renderer handles Python/Node.js/Go configs
- **Safe** — `StrictUndefined` raises errors on missing template variables
- **Hackathon-friendly** — templates are plain text, easy to edit

---

## Installation

```bash
# Clone the repo
git clone <repo-url>
cd ebready

# Install ebkit
pip install -e ".[dev]"

# Verify
ebkit --help
```

---

## Configuration & Security

### API Key Setup (Security-First Design)

EBKit adheres to a strict **zero-credential-exposure policy**:
- **No interactive terminal prompts:** EBKit intentionally does not prompt for API keys in the console to prevent credentials from being logged in terminal scrollback, history, or shoulder-surfed.
- **No plaintext disk storage:** EBKit strictly forbids storing API keys in configuration files (such as `~/.ebkit/config`) or caching credentials locally.
- **Environment variables only:** API keys must be injected via standard environment variables. This ensures compatibility with CI/CD runners (e.g., GitHub Actions, AWS CodeBuild) and keeps secrets out of version control.

Before running `ebkit init`, set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`):

**Linux / macOS:**
```bash
export GEMINI_API_KEY="your-api-key-here"
```

**Windows (PowerShell):**
```powershell
$env:GEMINI_API_KEY = "your-api-key-here"
```

**Windows (Command Prompt):**
```cmd
set GEMINI_API_KEY=your-api-key-here
```

If neither `GEMINI_API_KEY` nor `GOOGLE_API_KEY` is detected in the environment, `ebkit init` will safely abort and display an error reminding you to set the variable.

---

## Usage

### Interactive Setup Wizard (Primary UX)

Run the interactive setup wizard with a single command:

```bash
ebkit init
```

The interactive wizard guides you step-by-step:

```text
$ ebkit init

🚀 Welcome to EBReady

Where is your project?

1. Current directory
2. GitHub repository
3. Local project path

Select [1]: 1
Project path: .

🤖 AI Provider

1. Gemini — ✅ Available
2. OpenAI — 🔜 Coming Later
3. Claude — 🔜 Coming Later
4. Groq API — 🔜 Coming Later

Select [1]: 1

🔍 Scanning project...

✓ Python detected
✓ FastAPI detected
✓ requirements.txt detected
✓ app/main.py detected
✓ Port 8080 detected

🤖 Running Gemini analysis...

✓ DeploymentConfig generated
✓ Pydantic validation passed
✓ Configuration validated

🤖 Docker AI (Gordon) generating Dockerfile...

✓ Dockerfile generated by Docker AI (Gordon)

📦 Generating deployment kit...

✓ Dockerfile
✓ .dockerignore
✓ .env.example
✓ Procfile

🔎 Validating generated artifacts...

✓ Dockerfile validation
✓ Cross-file validation
✓ Secret validation

🐳 Docker Build

✓ linux/amd64 image built

🛡 Docker Scout

✓ Security scan completed
✓ 0 Critical
✓ 0 High
✓ Security gate passed
✓ Cluster Mode preflight

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ PROJECT READY FOR DEPLOYMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Saved Configuration (`~/.ebkit/config`)

Non-secret preferences (`ai_provider`) are saved locally so subsequent runs are instant:

```text
$ ebkit init

🚀 Welcome to EBReady

Where is your project?

1. Current directory
2. GitHub repository
3. Local project path

Select [1]: 1
Project path: .

Using saved configuration:

AI Provider: Gemini

Continue? [Y/n]: y
```

### GitHub Repositories

Analyze remote GitHub repositories directly:

```text
Select [1]: 2
GitHub repository URL:
> https://github.com/user/my-project
```

- Safe URL validation protects against command injection, path traversal, and malicious protocols
- Repository is cloned safely into a temporary directory without executing any untrusted code
- Cloned project runs through the exact same Day 2 scanner, Gemini analyzer, Pydantic validator, and Jinja2 renderer
- Temporary clone data is cleanly removed after processing

### Advanced CLI Options (CI/CD & Automation)

For automated pipelines and scripts, all parameters can be passed as CLI flags:

```bash
# Non-interactive CI run
ebkit init --path ./my-project --yes

# Remote repository with custom output folder
ebkit init --repo https://github.com/user/my-project --output ./generated --yes

# Dry run (displays deployment plan without writing files)
ebkit init --dry-run
```

```text
Options:
  -p, --path PATH             Path to the project repo root
  -r, --repo TEXT             GitHub or Git repo URL to clone and analyze
  -o, --output PATH           Output directory (defaults to project root)
  -a, --analyzer TEXT         AI backend: gemini | google | openai | claude | groq
  --dry-run                   Display deployment plan without writing any files
  -f, --force                 Overwrite existing files without prompting
  -y, --yes                   Non-interactive mode (skips prompts)
  --build / --no-build        Run local Docker build verification
  --runtime / --no-runtime    Run container and probe / and /health endpoints
  --scout / --no-scout        Run Docker Scout CVE scan and security gate
  --max-critical INT          Max allowed critical CVEs for gate [default: 0]
  --max-high INT              Max allowed high CVEs for gate [default: 5]
  --max-repair-attempts INT   Max automatic repairs via Docker AI (Gordon) [default: 2]
```

### Docker AI (Gordon) Self-Healing Recovery Loop

When Docker Build, Container Runtime Health Check, or Docker Scout security scan fails, EBReady leverages the installed **Docker AI (Gordon)** agent to diagnose and repair the issue automatically:

```text
Failure (Build error / Runtime crash / Scout Critical CVEs)
       │
       ▼
 🤖 Gordon Diagnoses Problem
       │
       ▼
 📄 Corrected Dockerfile Generated (Source code untouched)
       │
       ▼
 🛡 EBReady Safety & Cross-File Validation
       │
       ▼
 🔄 Rebuild & Retest (Limited to 2–3 attempts)
```

**Guardrails & Safety Guarantees:**
1. **Source Code Untouched:** Docker AI only generates and modifies `Dockerfile`. Application source code (`app/`, `requirements.txt`, etc.) is never modified.
2. **Pre-Rebuild Safety Validation:** All repaired Dockerfiles pass `CrossFileValidator` before executing `docker build`.
3. **Secret Protection:** `.env` and sensitive environment variable values are never passed to Gordon or copied into the container.
4. **Scout Security Gate:** Critical vulnerabilities must be 0 (`max_critical=0`). If base images contain critical CVEs, Gordon updates to hardened minimal images.
5. **Clear Availability Reporting:** If Docker AI (Gordon) is unavailable, EBReady explicitly reports `⚠️ Docker AI (Gordon) is unavailable` rather than claiming a repair succeeded.
6. **Bounded Attempts:** Repairs are strictly capped at 2–3 attempts (configured via `--max-repair-attempts`).

---

## Generated Files (Cluster Mode)

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-language Docker build (Python/Node.js), platform-aware (`linux/amd64`), non-root user |
| `.dockerignore` | Excludes `.git`, `.env*`, `__pycache__`, local venvs, node_modules; preserves app source |
| `Procfile` | Elastic Beanstalk web process definition (conditional) |
| `.ebignore` | Excludes dev files, secrets, and CI artefacts from EB bundles; preserves build files |
| `.env.example` | Documents required env var KEYS only — never contains values or secrets |

> **Note on `.ebextensions`:** Traditional Elastic Beanstalk `.ebextensions` configuration files rely on EC2/ALB `option_settings` namespaces (such as `aws:elasticbeanstalk:application:environment`). Because EB Cluster Mode runs on Amazon EKS container infrastructure, `.ebextensions` are ignored and not generated in Cluster Mode.

---

## AI Providers

| Provider | Status | Notes |
|----------|--------|-------|
| `Gemini` | ✅ Available (Default) | Uses `gemini-2.5-flash` (`GEMINI_API_KEY` or `GOOGLE_API_KEY`) |
| `OpenAI` | 🔜 Coming Later | Architecture ready for OpenAI integration |
| `Claude` | 🔜 Coming Later | Architecture ready for Anthropic Claude integration |
| `Groq API` | 🔜 Coming Later | Architecture ready for Groq ultra-fast LPU inference |

Only Gemini is active for deployment generation. Selecting upcoming providers displays `Coming Later`. Silent fallbacks are prohibited.

---

## Safety Behaviour

- **Existing files protected** — `Dockerfile`, `.dockerignore`, `Procfile`, `.ebignore`, `.env.example`, `.env` are never silently overwritten. You are prompted for each.
- **Secrets never exposed** — `.env` files are detected but never read. Only key names appear in `.env.example`.
- **API keys never stored** — `~/.ebkit/config` strictly forbids saving API keys, tokens, or credentials. Only non-sensitive preferences (`ai_provider`) are retained.
- **AWS credentials never requested** — EBReady never asks for AWS access keys or secret keys. In Day 2, AWS configuration questions are omitted entirely from `ebkit init` and deferred to Day 3 (`ebkit deploy`). Standard AWS CLI configuration, IAM roles, and environment variables are preserved.
- **Safe remote repository execution** — Remote GitHub URLs are strictly validated to block command injection and path traversal; cloned repos are analyzed without executing repository code, and temporary data is purged.
- **No shell injection** — `start_command` is validated to reject `$()`, backticks, `&&`, `||`, pipes, and semicolons.
- **No auto-execution** — generated commands are never run automatically.
- **Pydantic validation** — every AI response is parsed and validated before reaching the template renderer.
- **Cluster Mode Preflight** — evaluates architecture, port, health check, container security, build, and scout results before declaring `READY FOR DEPLOYMENT`.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```text
ebready/
├── ebkit/
│   ├── __main__.py             # Direct execution (python -m ebkit)
│   ├── cli.py                  # CLI entry point (ebkit command)
│   ├── config.py               # Non-secret config management (~/.ebkit/config)
│   ├── repo_handler.py         # Safe GitHub URL validation & repository cloner
│   ├── analyzer/
│   │   ├── scanner.py          # Heuristic repository scanner
│   │   └── ai_analyzer.py      # AI abstraction + Gemini (Active), OpenAI/Claude/Groq (Stubs)
│   ├── models/
│   │   ├── artifact_plan.py    # Strict controlled ArtifactPlan
│   │   └── deployment_config.py # Strict Pydantic schema + Cluster configs
│   ├── generator/
│   │   ├── artifact_planner.py # Artifact requirement planner
│   │   ├── renderer.py         # Jinja2 template renderer
│   │   └── templates/
│   │       ├── Dockerfile.j2
│   │       ├── dockerignore.j2
│   │       ├── Procfile.j2
│   │       ├── ebignore.j2
│   │       └── env.example.j2
│   ├── validator/
│   │   ├── cross_validator.py  # Cross-artifact consistency & secret scanning
│   │   ├── docker_validator.py # Docker build, runtime probe & Scout gate
│   │   └── cluster_preflight.py # Cluster Mode preflight validation
│   └── commands/
│       └── init.py             # ebkit init interactive setup wizard & pipeline
├── tests/
│   ├── test_ebkit.py           # Core scanner, models, renderer tests
│   ├── test_day2_artifacts.py  # Complete Day 2 artifact test matrix
│   └── test_interactive_cli.py # Interactive CLI UX, config & security tests
├── app/                        # EBReady FastAPI hello-world app
│   └── main.py
├── requirements.txt
└── pyproject.toml
```

---

## Day 2 — Complete Deployment Kit Generator & Validator ✅

### Supported Deployment Artifacts
1. **Dockerfile**: Production-oriented, multi-stage or single-stage, non-root execution (`appuser:appgroup`), no `:latest`, architecture-targeted (`linux/amd64`), clean caches.
2. **.dockerignore**: Excludes `.git`, `.env*`, `__pycache__`, local venvs, node_modules; guarantees required source files and entrypoints remain included.
3. **Procfile**: Derives production web start command directly from validated `DeploymentConfig`.
4. **.ebignore**: Prevents packaging local development and test files; guarantees Dockerfile, Procfile, and dependency manifests are preserved.
5. **.env.example**: Automatically extracts referenced environment variable keys only; zero secret values.

### Multi-Stage Validation & Security Gate
- **Cross-File Validator**: Runs 11 cross-file consistency checks including port matching, dependency file COPY verification, ignore pattern conflict checks, secret scanning, and Cluster Mode config consistency.
- **Docker Build Validator**: Executes real `docker build` with target platform to verify clean container compilation.
- **Docker Runtime Validator**: Starts container, probes `GET /` and `GET /health` with retries, asserts HTTP 200.
- **Docker Scout Security Gate**: Scans images for CVE vulnerabilities and enforces configurable security gates (0 critical, max 5 high). Distinguishes `BUILD SUCCESS` from `SECURITY VALIDATION`.
- **Cluster Mode Preflight**: Verifies target environment alignment (Region: `us-east-2`, App: `ebready`, Env: `ebready-dev`, Port: `8080`, Health: `/health`, Arch: `amd64`) before issuing `READY FOR DEPLOYMENT`.
- **Human Approval**: Interactive summary showing project analysis, DeploymentConfig, and artifact rationale before writing; never silently overwrites files.

---

## PLANNED (Day 3+)

- `ebkit deploy` — interactive AWS configuration (Region, Application, Environment), push to ECR, and trigger EB update
- GitHub Actions workflow generation
- AI failure recovery (diagnose failed deployments)
- Multi-service / monorepo support
