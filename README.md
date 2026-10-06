# EBReady

EBReady is a platform designed to simplify deploying applications to AWS Elastic Beanstalk Cluster Mode environments.

---

## Day 1 Objective

Deploy a minimal EBReady FastAPI application to an existing AWS Elastic Beanstalk Cluster Mode environment.

---

## AWS Environment Details

- **Region:** `us-east-2`
- **Application Name:** `ebready`
- **Environment Name:** `ebready-dev`
- **Environment Mode:** Cluster (Amazon EKS)
- **Live URL:** `http://ebready-dev.eba-unb4v5ht.us-east-2.elasticbeanstalk.com`
- **Direct ALB Endpoint:** `https://1e1b27bdf9cf6e87-ebready-dev-817832842.us-east-2.elb.amazonaws.com`

---

## COMPLETED (Day 1)

1. **AWS Setup Verification:**
   - Verified IAM user caller identity (`ebready-agent`).
   - Verified AWS region (`us-east-2`).
   - Confirmed existing `ebready-dev` Elastic Beanstalk Cluster Mode environment status and health.

2. **Minimal EBReady Application:**
   - Built a minimal FastAPI application exposing:
     - `GET /` -> `{"service": "EBReady", "message": "EBReady is running on AWS Elastic Beanstalk"}`
     - `GET /health` -> `{"status": "ok", "service": "ebready"}`
   - Configured dynamic `PORT` handling.

3. **Local Testing:**
   - Installed dependencies (`fastapi`, `uvicorn`).
   - Tested endpoints locally via `TestClient` and HTTP server.

4. **Cluster Mode Packaging & Deployment:**
   - Containerized application via `Dockerfile` for `linux/amd64`.
   - Pushed container image to Amazon ECR: `717056864326.dkr.ecr.us-east-2.amazonaws.com/ebready:v1.0.0`.
   - Created application version `v1.0.0-ebready` with `ImageSource`.
   - Deployed version to existing `ebready-dev` environment via `UpdateEnvironment`.

5. **Live Verification:**
   - Verified environment status transitioned to `Ready` and health to `Green` / `Ok`.
   - Tested live ALB and CNAME endpoints for both `/` and `/health`.

---

## PLANNED (Day 2+)

- GitHub repository ingestion
- AI repository analysis
- Automatic deployment pipeline
- GitHub Actions workflow
- AI failure recovery
- AI cost advisor
