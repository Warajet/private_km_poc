# Private KM Chatbot — Backend API

FastAPI service that provides enterprise knowledge retrieval via Google Cloud Vertex AI Search (Discovery Engine). Users authenticate with their HWC identity; every query is answered by a single `answer_query()` call that spans all datastores the caller is authorised to read, with two-layer access control:

1. **API layer** — `DatastoreRoutingService` selects which bucket datastores to include as `SearchRequest.DataStoreSpec` entries based on the user's department, relate-group membership, and JD code.
2. **ACL layer** — Discovery Engine enforces `acl_info` on every document using Domain-wide Delegation (DWD), impersonating the caller's GCP department identity (`{dept}@{workspace_domain}`).

## Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Local development](#local-development)
- [Running tests](#running-tests)
- [Docker](#docker)
- [Deploy to Cloud Run](#deploy-to-cloud-run)
- [Configuration reference](#configuration-reference)
- [Data ingestion](#data-ingestion)

---

## Architecture

```
Frontend / HWC app
      │  JWT (HS256)
      ▼
FastAPI  /api/v1/chat
      │
      ├─ DatastoreRoutingService   ← Layer 1: which buckets can this user read?
      │    Resolves List[DatastoreTarget]:
      │      PUBLIC        — always included
      │      INTERNAL-{dept} — user's own dept only
      │      RELATE-{id}   — groups the user belongs to
      │      CONFIDENTIAL  — only if user has a JD code
      │
      └─ DiscoveryEngineService    ← Layer 2: single answer_query() call
           ConversationalSearchServiceClient.answer_query(
               serving_config  = engine_serving_config,
               search_spec     = SearchSpec(SearchParams(
                   data_store_specs = [DataStoreSpec(ds, filter?), ...]
               )),
               session         = de_session_name,   # multi-turn
               credentials     = DWD(dept@domain),  # impersonated
           )
           Discovery Engine enforces acl_info per document.
           Gemini generates one grounded answer across all buckets.
```

**Datastore bucket layout**

| Bucket | Count | Who can query |
|---|---|---|
| Public | 1 | All authenticated users |
| Internal | N (one per dept) | Members of that department |
| Relate | K (one per cross-dept group) | Members of that relate group |
| Confidential | 1 | Users with a matching JD code |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | |
| GCP project | With billing enabled |
| Vertex AI Search engine | Engine must have all bucket datastores attached |
| Service Account | With DWD enabled in Google Workspace Admin |
| SA roles | `roles/discoveryengine.viewer`, `roles/iam.serviceAccountTokenCreator` |
| Google Workspace domain | Users identified by `{dept}@{domain}` group emails |

### GCP setup checklist

```bash
# 1. Enable APIs
gcloud services enable discoveryengine.googleapis.com \
    iam.googleapis.com \
    iamcredentials.googleapis.com

# 2. Create a service account
gcloud iam service-accounts create km-chatbot-sa \
    --display-name="KM Chatbot Service Account"

# 3. Grant Discovery Engine access
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:km-chatbot-sa@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/discoveryengine.viewer"

# 4. Grant token creator (needed for Workload Identity impersonation on Cloud Run)
gcloud iam service-accounts add-iam-policy-binding \
    km-chatbot-sa@$PROJECT_ID.iam.gserviceaccount.com \
    --member="serviceAccount:km-chatbot-sa@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountTokenCreator"

# 5. Create a Vertex AI Search App (engine) in GCP Console
#    → Vertex AI Search → Apps → Create App → Search → Generic
#    → Attach all bucket datastores to the engine
#    → Note the engine ID shown in the URL / app details

# 6. For local dev only — download a SA key
gcloud iam service-accounts keys create sa-key.json \
    --iam-account=km-chatbot-sa@$PROJECT_ID.iam.gserviceaccount.com
```

In Google Workspace Admin, enable Domain-wide Delegation for the SA client ID with scope:
```
https://www.googleapis.com/auth/cloud-platform
```

---

## Local development

```bash
cd backend

# 1. Create virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest, ruff, mypy

# 3. Copy and edit config
cp .env.example .env
# Edit .env — set at minimum:
#   GCP_PROJECT_ID, DISCOVERY_ENGINE_ENGINE_ID, WORKSPACE_DOMAIN,
#   GOOGLE_APPLICATION_CREDENTIALS (path to sa-key.json), JWT_SECRET

# 4. Prepare config files (first time)
mkdir -p config
cp scripts/datastore_registry.json config/datastore_registry.json
# Edit config/datastore_registry.json — replace placeholder datastore IDs
#   with your actual Discovery Engine datastore IDs.

cp scripts/seed_user_mapping.json config/user_mapping.json
# Edit config/user_mapping.json — add your HWC user emails with their
#   department, jd_code, and relate_groups.

# Update .env to point at these files:
#   DATASTORE_REGISTRY_FILE=config/datastore_registry.json
#   USER_MAPPING_FILE=config/user_mapping.json

# 5. Run the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API docs (only available when `DEBUG=true`):
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/api/v1/health

### Key endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness check |
| `POST` | `/api/v1/auth/token` | Exchange HWC credentials for a JWT |
| `POST` | `/api/v1/chat` | Send a message, get a grounded answer |
| `GET` | `/api/v1/chat/sessions` | List sessions for the caller |
| `GET` | `/api/v1/chat/sessions/{id}` | Retrieve message history |
| `DELETE` | `/api/v1/chat/sessions/{id}` | Delete a session |

---

## Running tests

```bash
cd backend
pytest                        # run all 64 tests
pytest -v                     # verbose output
pytest tests/test_chat_controller.py   # single file
```

All tests mock GCP clients — no live GCP credentials are required.

---

## Docker

```bash
cd backend

# Build
docker build -t km-chatbot-api .

# Run locally (mount config files)
docker run --rm -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/config:/app/config:ro \
  km-chatbot-api
```

The image runs as a non-root user (`appuser`) and exposes port 8000.

---

## Deploy to Cloud Run

### 1. Build and push the image

```bash
export PROJECT_ID=your-gcp-project-id
export REGION=asia-southeast1          # choose your region
export IMAGE=gcr.io/$PROJECT_ID/km-chatbot-api

cd backend
docker build -t $IMAGE .
docker push $IMAGE
```

Or use Cloud Build (no local Docker required):

```bash
cd backend
gcloud builds submit --tag $IMAGE .
```

### 2. Upload config files to Secret Manager

Store the two JSON config files as secrets so Cloud Run can mount them:

```bash
# Datastore registry
gcloud secrets create km-datastore-registry \
    --data-file=config/datastore_registry.json

# User mapping
gcloud secrets create km-user-mapping \
    --data-file=config/user_mapping.json

# Grant the Cloud Run SA access to both secrets
SA=km-chatbot-sa@$PROJECT_ID.iam.gserviceaccount.com

gcloud secrets add-iam-policy-binding km-datastore-registry \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding km-user-mapping \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
```

### 3. Deploy

```bash
gcloud run deploy km-chatbot-api \
  --image=$IMAGE \
  --region=$REGION \
  --service-account=km-chatbot-sa@$PROJECT_ID.iam.gserviceaccount.com \
  --platform=managed \
  --no-allow-unauthenticated \
  --min-instances=1 \
  --max-instances=10 \
  --memory=512Mi \
  --cpu=1 \
  --timeout=60 \
  --concurrency=80 \
  --set-secrets="/app/config/datastore_registry.json=km-datastore-registry:latest,/app/config/user_mapping.json=km-user-mapping:latest" \
  --set-env-vars="\
GCP_PROJECT_ID=$PROJECT_ID,\
GCP_LOCATION=global,\
DISCOVERY_ENGINE_ENGINE_ID=your-engine-id,\
DISCOVERY_ENGINE_SERVING_CONFIG_ID=default_serving_config,\
WORKSPACE_DOMAIN=hello.org,\
DATASTORE_REGISTRY_FILE=/app/config/datastore_registry.json,\
USER_MAPPING_FILE=/app/config/user_mapping.json,\
SESSION_BACKEND=memory,\
ENVIRONMENT=production,\
DEBUG=false,\
LOG_LEVEL=INFO,\
GEMINI_MODEL=gemini-2.0-flash-001,\
ALLOWED_ORIGINS=https://your-frontend-domain.com" \
  --update-secrets="JWT_SECRET=km-jwt-secret:latest"
```

> **Note — `GOOGLE_APPLICATION_CREDENTIALS`**: Do **not** set this on Cloud Run. The service uses Application Default Credentials (ADC) automatically via the attached service account. DWD impersonation is performed through the IAM Credentials API (`roles/iam.serviceAccountTokenCreator`).

> **Note — JWT secret**: Store it in Secret Manager before deploying:
> ```bash
> echo -n "$(openssl rand -base64 48)" | \
>   gcloud secrets create km-jwt-secret --data-file=-
> ```

### 4. Verify

```bash
SERVICE_URL=$(gcloud run services describe km-chatbot-api \
    --region=$REGION --format='value(status.url)')

curl $SERVICE_URL/api/v1/health
# → {"status": "ok", ...}
```

### 5. Scaling and production options

**Redis session backend** — in-memory sessions are lost on container restart. For production, provision a Memorystore (Redis) instance and set:
```bash
--set-env-vars="SESSION_BACKEND=redis,REDIS_URL=redis://10.x.x.x:6379/0"
```
Grant the Cloud Run SA the `roles/redis.viewer` role and ensure VPC connector is configured.

**Min instances** — set `--min-instances=1` to avoid cold-start latency on the first request after idle.

**Cloud Armor / IAP** — put Cloud Armor in front of the Cloud Run service or use Identity-Aware Proxy (IAP) to restrict access to your corporate network / Google Workspace users.

---

## Configuration reference

All settings are loaded from environment variables (or a `.env` file in local dev).

| Variable | Required | Default | Description |
|---|---|---|---|
| `GCP_PROJECT_ID` | ✅ | — | GCP project ID |
| `GCP_LOCATION` | | `global` | Discovery Engine location |
| `DISCOVERY_ENGINE_ENGINE_ID` | ✅ | — | Vertex AI Search App (engine) ID |
| `DISCOVERY_ENGINE_SERVING_CONFIG_ID` | | `default_serving_config` | Serving config on the engine |
| `DATASTORE_REGISTRY_FILE` | ✅ | — | Path to `datastore_registry.json` |
| `USER_MAPPING_FILE` | ✅ | — | Path to `user_mapping.json` |
| `WORKSPACE_DOMAIN` | | `hello.org` | Google Workspace domain |
| `DWD_SCOPES` | | `cloud-platform` | Comma-separated OAuth scopes for DWD |
| `GOOGLE_APPLICATION_CREDENTIALS` | local only | — | SA key JSON path (omit on Cloud Run) |
| `GEMINI_MODEL` | | `gemini-2.0-flash-001` | Gemini model used by answer_query |
| `GEMINI_SYSTEM_PROMPT` | | built-in | System prompt for Gemini |
| `JWT_SECRET` | ✅ | — | HS256 signing key for internal JWTs |
| `JWT_ALGORITHM` | | `HS256` | JWT algorithm |
| `JWT_EXPIRY_MINUTES` | | `60` | JWT lifetime |
| `SESSION_BACKEND` | | `memory` | `memory` or `redis` |
| `REDIS_URL` | redis only | — | e.g. `redis://host:6379/0` |
| `SESSION_TTL_SECONDS` | | `3600` | Session expiry |
| `ALLOWED_ORIGINS` | | `*` | Comma-separated CORS origins |
| `ENVIRONMENT` | | `local` | `local`, `staging`, `production` |
| `DEBUG` | | `false` | Enables `/docs`, `/redoc`, `/openapi.json` |
| `LOG_LEVEL` | | `INFO` | Python log level |

---

## Data ingestion

Datastore documents are NDJSON files with `structData` and `acl_info` fields.

### File format

```jsonc
// Public document — no acl_info needed
{"id": "pub-001", "structData": {"title": "...", "content": "...", "access_level": "public"}}

// Internal document — acl_info restricts to dept A identity
{"id": "int-a-001", "structData": {"title": "...", "access_level": "internal", "department": "A"},
 "acl_info": {"readers": [{"principals": [{"userId": "A@hello.org"}]}]}}

// Relate document — acl_info uses a Google Group
{"id": "rel-ab-001", "structData": {"title": "...", "access_level": "relate", "relate_id": "ab"},
 "acl_info": {"readers": [{"principals": [{"groupId": "relate-ab@hello.org"}]}]}}

// Confidential document — acl_info + structData filter fields
{"id": "conf-001",
 "structData": {"title": "...", "access_level": "confidential", "department": "A",
                "required_jd_code": "ENG001"},
 "acl_info": {"readers": [{"principals": [{"userId": "A@hello.org"}]}]}}
```

Sample files are in `backend/scripts/`:

| File | Bucket |
|---|---|
| `sample_public.ndjson` | Public |
| `sample_internal_dept_a.ndjson` | Internal — dept A |
| `sample_internal_dept_b.ndjson` | Internal — dept B |
| `sample_relate_ab.ndjson` | Relate — group ab |
| `sample_confidential.ndjson` | Confidential |

### Import

```bash
# Import a file into its datastore
gcloud alpha discoveryengine documents import \
    --project=$PROJECT_ID \
    --location=global \
    --collection=default_collection \
    --data-store=public-datastore \
    --source-type=cloud-storage \
    --input-config='{"gcsSource": {"inputUris": ["gs://your-bucket/sample_public.ndjson"], "dataSchema": "document"}}'

# Or use the helper script (requires GOOGLE_APPLICATION_CREDENTIALS)
cd backend
python scripts/import_documents.py \
    --project $PROJECT_ID \
    --datastore public-datastore \
    --file scripts/sample_public.ndjson
```

### `datastore_registry.json` schema

```json
{
  "public":       "public-datastore-id",
  "internal":     { "A": "internal-dept-a-id", "B": "internal-dept-b-id" },
  "relate":       { "ab": "relate-ab-id", "abc": "relate-abc-id" },
  "confidential": "confidential-datastore-id"
}
```

Department keys must match the `department` values in `user_mapping.json`.  
Relate keys must match the `relate_groups` entries in `user_mapping.json`.

### `user_mapping.json` schema

```json
{
  "user@hello.org": {
    "department": "A",
    "jd_code": "ENG001",
    "display_name": "User Name",
    "relate_groups": ["ab"]
  }
}
```
