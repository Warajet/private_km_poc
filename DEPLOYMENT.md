# Deployment Guide — GCP Cloud Run + Gemini Enterprise

This guide walks through everything needed to take the app from your laptop to a production Cloud Run service backed by Gemini Enterprise (Vertex AI) and Cloud SQL (PostgreSQL).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [GCP Project Setup](#3-gcp-project-setup)
4. [Vertex AI — Gemini Enterprise](#4-vertex-ai--gemini-enterprise)
5. [Artifact Registry — Container Images](#5-artifact-registry--container-images)
6. [Build and Push the Docker Image](#6-build-and-push-the-docker-image)
7. [Database — Cloud SQL PostgreSQL](#7-database--cloud-sql-postgresql)
8. [Deploy to Cloud Run](#8-deploy-to-cloud-run)
9. [Environment Variables Reference](#9-environment-variables-reference)
10. [Custom Domain (Optional)](#10-custom-domain-optional)
11. [CI/CD with Cloud Build (Optional)](#11-cicd-with-cloud-build-optional)
12. [Monitoring and Logs](#12-monitoring-and-logs)
13. [Cost Estimates](#13-cost-estimates)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Architecture Overview

```
Users (browser)
      │ HTTPS
      ▼
Cloud Run Service  ─── Vertex AI API (Gemini Enterprise)
      │
      │ Unix socket (no TCP overhead, no VPC needed)
      ▼
Cloud SQL (PostgreSQL)
```

**Key points:**
- Cloud Run is serverless — scales to zero when no one is using it, scales out under load
- Authentication to Vertex AI uses the Cloud Run **service account** (Workload Identity) — no API keys or credential files in the container
- Cloud SQL connects via a **Unix socket** injected by the Cloud SQL Auth Proxy built into Cloud Run — no VPC, no IP allowlisting needed

---

## 2. Prerequisites

Install the following tools on your local machine:

```bash
# Google Cloud CLI
# https://cloud.google.com/sdk/docs/install
gcloud --version   # must be >= 450.0.0

# Docker Desktop (or Docker Engine on Linux)
docker --version   # must be >= 24.0

# Node.js 20+ (for local testing)
node --version     # must be >= 20.0
```

Log in and set your project:

```bash
gcloud auth login
gcloud auth configure-docker   # allows Docker to push to Google registries
```

---

## 3. GCP Project Setup

### 3.1 Set your project ID

```bash
# Replace with your actual GCP project ID
export PROJECT_ID="your-project-id"
export REGION="asia-southeast1"          # change to your preferred region
export SERVICE_NAME="private-km"

gcloud config set project $PROJECT_ID
```

> **Tip:** List available regions for Cloud Run: `gcloud run regions list`

### 3.2 Enable required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com
```

This takes 1–2 minutes. Each API only needs to be enabled once per project.

### 3.3 Create a dedicated service account

The Cloud Run service needs its own identity with only the permissions it requires (principle of least privilege).

```bash
export SA_NAME="private-km-sa"
export SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Create the service account
gcloud iam service-accounts create $SA_NAME \
  --display-name="Private KM Cloud Run Service Account"
```

Grant the minimum required roles:

```bash
# Send requests to Vertex AI (Gemini Enterprise)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/aiplatform.user"

# Read secrets from Secret Manager (JWT_SECRET, etc.)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"

# Connect to Cloud SQL via the built-in proxy
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/cloudsql.client"
```

---

## 4. Vertex AI — Gemini Enterprise

Vertex AI is Google's enterprise platform for Gemini models. Unlike the Google AI Studio API key approach, authentication is handled automatically by GCP Identity — no keys to manage or rotate.

### 4.1 Confirm Vertex AI is available in your region

Gemini models are not available in every region. Check availability:
- Go to **Vertex AI > Model Garden** in the Cloud Console
- Search for "Gemini" and note which regions appear
- Common supported regions: `us-central1`, `us-east4`, `europe-west4`, `asia-southeast1`

```bash
# Set the region where Vertex AI Gemini is available in your project
export VERTEX_REGION="us-central1"   # adjust if needed
```

### 4.2 How authentication works on Cloud Run

When the app is running on Cloud Run with the service account from §3.3, the `@google-cloud/vertexai` SDK automatically obtains credentials via **Application Default Credentials (ADC)**. No credential file, no API key — GCP handles it at the infrastructure level.

The `lib/gemini.ts` file in this project already auto-detects this:
- `GOOGLE_CLOUD_PROJECT` is set → uses Vertex AI (production path)
- `GEMINI_API_KEY` is set → uses Google AI Studio (local dev path)

### 4.3 Choose your Gemini model

| Model | Best for | Notes |
|---|---|---|
| `gemini-1.5-pro` | High quality, complex reasoning | Default |
| `gemini-1.5-flash` | Speed and cost efficiency | ~5x cheaper than Pro |
| `gemini-2.0-flash` | Latest generation, fast | Check regional availability |

Set `GEMINI_MODEL` in your Cloud Run environment to override the default.

### 4.4 Verify access (optional local test)

```bash
# Authenticate with your personal credentials to test from your laptop
gcloud auth application-default login

# Quick test: list models available in your project
gcloud ai models list --region=$VERTEX_REGION
```

---

## 5. Artifact Registry — Container Images

Artifact Registry is Google's private Docker registry. It keeps your images in the same region as Cloud Run for fast pulls.

```bash
export REGISTRY="${REGION}-docker.pkg.dev"
export REPO_NAME="private-km"
export IMAGE="${REGISTRY}/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}"

# Create the repository (only once)
gcloud artifacts repositories create $REPO_NAME \
  --repository-format=docker \
  --location=$REGION \
  --description="Private KM container images"

# Allow Docker to authenticate to this registry
gcloud auth configure-docker ${REGION}-docker.pkg.dev
```

---

## 6. Build and Push the Docker Image

### 6.1 Build locally and push

```bash
# From the project root directory
docker build \
  --platform linux/amd64 \          # Cloud Run runs on amd64 (important on Apple Silicon)
  --tag ${IMAGE}:latest \
  .

docker push ${IMAGE}:latest
```

> **On Apple Silicon (M1/M2/M3 Mac):** The `--platform linux/amd64` flag is mandatory. Without it, Docker builds an ARM image that will not run on Cloud Run.

### 6.2 Build with Cloud Build (alternative — no local Docker needed)

Cloud Build runs in GCP and pushes directly to Artifact Registry:

```bash
gcloud builds submit \
  --tag ${IMAGE}:latest \
  --machine-type=e2-highcpu-8 \
  .
```

---

## 7. Database — Cloud SQL PostgreSQL

> **Why not SQLite?**
> Cloud Run containers have an **ephemeral filesystem**. Any data written to disk (including `./data/chat.db`) is lost when the container restarts or a new revision is deployed. For persistent user data, you must use an external database.

### 7.1 Create the Cloud SQL instance

```bash
export DB_INSTANCE="private-km-db"
export DB_NAME="private_km"
export DB_USER="private_km_user"

# Create a PostgreSQL 16 instance (smallest tier for a POC)
# db-f1-micro: 1 vCPU, 614 MB RAM, ~$7–10/month
gcloud sql instances create $DB_INSTANCE \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=$REGION \
  --storage-type=SSD \
  --storage-size=10GB \
  --no-backup \          # enable backups in production: remove this flag
  --deletion-protection  # prevents accidental deletion

# Create the database
gcloud sql databases create $DB_NAME \
  --instance=$DB_INSTANCE

# Create the application user with a strong password
# Store the password securely — you will need it for DATABASE_URL
gcloud sql users create $DB_USER \
  --instance=$DB_INSTANCE \
  --password="REPLACE_WITH_STRONG_PASSWORD"
```

### 7.2 Create the database schema

Connect to the instance via Cloud SQL Auth Proxy and run the DDL:

```bash
# Start the proxy in the background
gcloud sql auth-proxy ${PROJECT_ID}:${REGION}:${DB_INSTANCE} &
PROXY_PID=$!

# Run the schema
psql "host=127.0.0.1 port=5432 dbname=${DB_NAME} user=${DB_USER} password=YOUR_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  username      TEXT UNIQUE NOT NULL,
  email         TEXT UNIQUE,
  password_hash TEXT NOT NULL,
  created_at    BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title      TEXT NOT NULL DEFAULT 'New Chat',
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

CREATE TABLE IF NOT EXISTS messages (
  id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role       TEXT NOT NULL CHECK(role IN ('user','assistant')),
  content    TEXT NOT NULL,
  created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
SQL

kill $PROXY_PID
```

### 7.3 Switch the app to PostgreSQL

The repo includes a ready-made PostgreSQL adapter at `lib/db-cloud-sql.ts`. Activate it:

```bash
# Replace the SQLite adapter with the PostgreSQL one
cp lib/db-cloud-sql.ts lib/db.ts

# Swap the database package
npm uninstall better-sqlite3 @types/better-sqlite3
npm install pg @types/pg
```

Remove the `better-sqlite3` webpack external from `next.config.js`:

```js
// next.config.js — after switching to PostgreSQL
/** @type {import('next').NextConfig} */
const nextConfig = {};
module.exports = nextConfig;
```

Rebuild and push the updated image:

```bash
docker build --platform linux/amd64 --tag ${IMAGE}:latest .
docker push ${IMAGE}:latest
```

### 7.4 Store secrets in Secret Manager

Never put passwords in environment variable plain text. Use Secret Manager instead:

```bash
# Store the database connection string
echo -n "postgresql://${DB_USER}:REPLACE_WITH_STRONG_PASSWORD@localhost/${DB_NAME}?host=/cloudsql/${PROJECT_ID}:${REGION}:${DB_INSTANCE}" | \
  gcloud secrets create DATABASE_URL --data-file=-

# Store the JWT signing secret (generate a strong random value)
openssl rand -hex 32 | \
  gcloud secrets create JWT_SECRET --data-file=-
```

Grant the service account access:

```bash
gcloud secrets add-iam-policy-binding DATABASE_URL \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding JWT_SECRET \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"
```

---

## 8. Deploy to Cloud Run

### 8.1 Initial deployment (SQLite version — quick demo)

Use this to verify the container runs correctly before connecting Cloud SQL:

```bash
gcloud run deploy $SERVICE_NAME \
  --image=${IMAGE}:latest \
  --region=$REGION \
  --service-account=$SA_EMAIL \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=10 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${VERTEX_REGION},GEMINI_MODEL=gemini-1.5-pro" \
  --set-env-vars="JWT_SECRET=REPLACE_WITH_TEMP_SECRET"
```

After deployment, Cloud Run prints the service URL. Open it in your browser.

> **Note:** With SQLite, data does not persist across restarts. Use this only for a quick smoke test.

### 8.2 Production deployment (Cloud SQL + Secret Manager)

```bash
export CLOUD_SQL_INSTANCE="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"

gcloud run deploy $SERVICE_NAME \
  --image=${IMAGE}:latest \
  --region=$REGION \
  --service-account=$SA_EMAIL \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=20 \
  --concurrency=80 \
  --add-cloudsql-instances=$CLOUD_SQL_INSTANCE \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --set-env-vars="GOOGLE_CLOUD_LOCATION=${VERTEX_REGION}" \
  --set-env-vars="GEMINI_MODEL=gemini-1.5-pro" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest" \
  --set-secrets="JWT_SECRET=JWT_SECRET:latest"
```

**Flag explanations:**

| Flag | Purpose |
|---|---|
| `--add-cloudsql-instances` | Injects a Unix socket at `/cloudsql/PROJECT:REGION:INSTANCE`; no VPC needed |
| `--set-secrets` | Mounts Secret Manager values as environment variables at startup |
| `--min-instances=1` | Keeps one instance warm to avoid cold-start latency for users |
| `--concurrency=80` | Each instance can serve up to 80 simultaneous streaming requests |
| `--allow-unauthenticated` | Makes the URL publicly accessible; the app enforces its own login |

### 8.3 Verify the deployment

```bash
# Get the service URL
gcloud run services describe $SERVICE_NAME \
  --region=$REGION \
  --format="value(status.url)"

# Stream live logs
gcloud run services logs tail $SERVICE_NAME --region=$REGION
```

---

## 9. Environment Variables Reference

| Variable | Required | Where to set | Description |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | Production | Cloud Run env | Your GCP project ID — triggers Vertex AI mode |
| `GOOGLE_CLOUD_LOCATION` | Production | Cloud Run env | Vertex AI region (e.g. `us-central1`) |
| `GEMINI_MODEL` | Optional | Cloud Run env | Model name (default: `gemini-1.5-pro`) |
| `SYSTEM_PROMPT` | Optional | Cloud Run env | Custom AI persona/instructions |
| `JWT_SECRET` | **Required** | Secret Manager | Random string ≥ 32 chars for signing tokens |
| `DATABASE_URL` | Production | Secret Manager | PostgreSQL connection string (Cloud SQL) |
| `GEMINI_API_KEY` | Local dev only | `.env.local` | Google AI Studio key — not used on Cloud Run |

---

## 10. Custom Domain (Optional)

```bash
# Map your domain to the Cloud Run service
gcloud run domain-mappings create \
  --service=$SERVICE_NAME \
  --domain=chat.yourcompany.com \
  --region=$REGION

# Cloud Run will print the DNS records to configure in your domain registrar.
# TLS is provisioned automatically (Google-managed certificate).
```

---

## 11. CI/CD with Cloud Build (Optional)

Create a `cloudbuild.yaml` in the project root to automatically build and deploy on every push to the `main` branch:

```yaml
# cloudbuild.yaml
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - build
      - '--platform=linux/amd64'
      - '--tag=${_IMAGE}:$COMMIT_SHA'
      - '--tag=${_IMAGE}:latest'
      - '.'

  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', '--all-tags', '${_IMAGE}']

  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
    entrypoint: gcloud
    args:
      - run
      - deploy
      - '${_SERVICE_NAME}'
      - '--image=${_IMAGE}:$COMMIT_SHA'
      - '--region=${_REGION}'
      - '--platform=managed'

substitutions:
  _SERVICE_NAME: private-km
  _REGION: asia-southeast1
  _IMAGE: asia-southeast1-docker.pkg.dev/YOUR_PROJECT_ID/private-km/private-km

images:
  - '${_IMAGE}:$COMMIT_SHA'
  - '${_IMAGE}:latest'

options:
  machineType: E2_HIGHCPU_8
  logging: CLOUD_LOGGING_ONLY
```

Connect the trigger:

```bash
# Link your GitHub repo to Cloud Build
gcloud builds triggers create github \
  --repo-name=private_km_poc \
  --repo-owner=YOUR_GITHUB_ORG \
  --branch-pattern="^main$" \
  --build-config=cloudbuild.yaml
```

---

## 12. Monitoring and Logs

### Live logs
```bash
gcloud run services logs tail $SERVICE_NAME --region=$REGION
```

### Cloud Monitoring dashboard
Navigate to **Cloud Console → Cloud Run → private-km → Metrics** to see:
- Request count and latency
- Container instance count (scale events)
- CPU and memory utilization

### Vertex AI quota and usage
Navigate to **Cloud Console → Vertex AI → Quotas** to monitor:
- Requests per minute per model
- Token usage (input + output)

### Set up an alert for errors
```bash
# Alert if error rate exceeds 5% over 5 minutes
gcloud alpha monitoring policies create \
  --notification-channels=YOUR_CHANNEL_ID \
  --display-name="Private KM 5xx Alert" \
  --condition-display-name="Error rate > 5%" \
  --condition-filter='resource.type="cloud_run_revision" AND metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class="5xx"'
```

---

## 13. Cost Estimates

All costs are approximate and depend on usage volume.

| Service | Configuration | Monthly cost |
|---|---|---|
| Cloud Run | 1 min instance, 512 MB, 1 vCPU, ~10k req/day | ~$5–15 |
| Cloud SQL | `db-f1-micro`, 10 GB SSD, PostgreSQL 16 | ~$8–12 |
| Artifact Registry | ~1 GB image storage | ~$0.10 |
| Secret Manager | 2 secrets, ~10k accesses/month | ~$0.01 |
| Vertex AI (Gemini 1.5 Pro) | depends on token volume — see below | variable |

**Vertex AI Gemini 1.5 Pro pricing (as of 2025):**
- Input: $1.25 per 1M tokens (prompts up to 128K)
- Output: $5.00 per 1M tokens
- A typical conversation turn ≈ 500–2000 tokens total
- 1000 messages/day ≈ $2–8/day in token costs

**Cost-saving tips:**
- Use `gemini-1.5-flash` instead of `gemini-1.5-pro` (5× cheaper, slightly lower quality)
- Set `--min-instances=0` if the tool is used infrequently (adds ~1s cold start)
- Enable Cloud SQL **automatic storage increase** instead of over-provisioning

---

## 14. Troubleshooting

### Container fails to start

```bash
# Check the revision logs for startup errors
gcloud run revisions logs $SERVICE_NAME --region=$REGION --limit=50
```

Common causes:
- **"GOOGLE_CLOUD_PROJECT not set"** → check `--set-env-vars` in the deploy command
- **"DATABASE_URL not set"** → check `--set-secrets` and Secret Manager access
- **Port mismatch** → ensure `--port=8080`; Cloud Run sets `PORT=8080` automatically

### "Permission denied" when calling Vertex AI

```bash
# Verify the service account has the Vertex AI User role
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --format="table(bindings.role,bindings.members)" \
  --filter="bindings.members:${SA_EMAIL}"
```

The output should include `roles/aiplatform.user`.

### Cloud SQL connection errors

```bash
# Verify the instance name is correct
gcloud sql instances list

# Check the Cloud SQL connection is added to the revision
gcloud run revisions describe $(gcloud run revisions list --service=$SERVICE_NAME --region=$REGION --format="value(name)" | head -1) \
  --region=$REGION \
  --format="value(spec.template.metadata.annotations)"
```

Look for `run.googleapis.com/cloudsql-instances` in the annotations.

### "Model not found" from Vertex AI

- Confirm the model name matches what is available in your region
- Check `gcloud ai models list --region=$VERTEX_REGION`
- Try `gemini-1.5-pro-001` (versioned alias) instead of `gemini-1.5-pro`

### Streaming responses cut off

Cloud Run has a **request timeout** (default: 5 minutes). For long AI responses:

```bash
gcloud run services update $SERVICE_NAME \
  --region=$REGION \
  --timeout=600   # 10 minutes
```

### Image build fails on Apple Silicon

Always pass `--platform linux/amd64`:
```bash
docker build --platform linux/amd64 --tag ${IMAGE}:latest .
```
