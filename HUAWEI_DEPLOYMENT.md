# Production Deployment Guide — Huawei Cloud CCE (Kubernetes)

This guide moves the application from the GCP Cloud Run PoC environment to a
production-grade deployment on **Huawei Cloud CCE** (Cloud Container Engine),
using **Huawei RDS** for the database and calling **Gemini Enterprise via
Vertex AI** over the internet with a GCP service account key.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Key Differences from the GCP PoC](#2-key-differences-from-the-gcp-poc)
3. [Prerequisites](#3-prerequisites)
4. [Huawei Cloud Infrastructure Setup](#4-huawei-cloud-infrastructure-setup)
   - 4.1 [VPC and Subnets](#41-vpc-and-subnets)
   - 4.2 [CCE Cluster](#42-cce-cluster)
   - 4.3 [SWR Container Registry](#43-swr-container-registry)
   - 4.4 [RDS PostgreSQL](#44-rds-postgresql)
5. [GCP Service Account for Vertex AI](#5-gcp-service-account-for-vertex-ai)
6. [Build and Push the Production Image](#6-build-and-push-the-production-image)
7. [Configure kubectl for CCE](#7-configure-kubectl-for-cce)
8. [Create Kubernetes Secrets](#8-create-kubernetes-secrets)
9. [Update Manifests for Your Environment](#9-update-manifests-for-your-environment)
10. [Deploy to CCE](#10-deploy-to-cce)
11. [TLS / Custom Domain](#11-tls--custom-domain)
12. [Verify the Deployment](#12-verify-the-deployment)
13. [Rotating Secrets](#13-rotating-secrets)
14. [Upgrading the Application](#14-upgrading-the-application)
15. [Scaling](#15-scaling)
16. [Monitoring and Observability](#16-monitoring-and-observability)
17. [Cost Considerations](#17-cost-considerations)
18. [Troubleshooting](#18-troubleshooting)

---

## 1. Architecture Overview

```
Internet
   │ HTTPS (443)
   ▼
Huawei ELB (Elastic Load Balancer)
   │ HTTP (80)
   ▼
CCE Ingress Controller (kube-system)
   │ HTTP (8080)
   ▼
private-km pods (×2–10, auto-scaled by HPA)
   │                          │
   │ TCP 5432 (VPC private)   │ HTTPS 443 (internet)
   ▼                          ▼
Huawei RDS PostgreSQL    Google Vertex AI (Gemini Enterprise)
```

**Components:**

| Component | Huawei Service | Purpose |
|---|---|---|
| Container registry | SWR (Software Repository for Container) | Stores Docker images |
| Kubernetes | CCE (Cloud Container Engine) | Runs the application |
| Database | RDS for PostgreSQL | Persistent chat data |
| Load balancer | ELB (Elastic Load Balancer) | HTTPS termination, traffic routing |
| Secrets | Kubernetes Secrets (+ optional DEW/CSMS) | Credentials at rest |
| AI API | Google Vertex AI (external call) | Gemini Enterprise responses |

---

## 2. Key Differences from the GCP PoC

| Concern | GCP Cloud Run (PoC) | Huawei CCE (Production) |
|---|---|---|
| Database | SQLite (ephemeral) | RDS PostgreSQL (persistent) |
| Vertex AI auth | Workload Identity (automatic) | GCP service account JSON key (explicit) |
| Scaling | Serverless (0→N) | HPA (minReplicas=2, always on) |
| Secrets | Secret Manager | Kubernetes Secrets (+ Huawei DEW optional) |
| Image registry | GCP Artifact Registry | Huawei SWR |
| Ingress | Cloud Run built-in | Huawei ELB Ingress Controller |
| Image | `Dockerfile` (SQLite) | `Dockerfile.production` (PostgreSQL) |

---

## 3. Prerequisites

Install the following tools:

```bash
# Huawei Cloud CLI
# https://support.huaweicloud.com/intl/en-us/devg-apisign/api-sign-provide.html
hcloud --version       # or use the web console for most steps

# kubectl
kubectl version --client  # must be >= 1.28

# Docker
docker --version          # must be >= 24.0

# helm (optional — for ingress controller installation)
helm version
```

---

## 4. Huawei Cloud Infrastructure Setup

All steps can be performed from the **Huawei Cloud Console**
(console.huaweicloud.com) or the `hcloud` CLI.

### 4.1 VPC and Subnets

Create a dedicated VPC with at least two subnets in different AZs for HA:

| Subnet | CIDR | Purpose |
|---|---|---|
| subnet-app-az1 | 192.168.1.0/24 | CCE nodes (AZ1) |
| subnet-app-az2 | 192.168.2.0/24 | CCE nodes (AZ2) |
| subnet-db | 192.168.3.0/24 | RDS PostgreSQL |

**Console path:** VPC → Create VPC → Add Subnets

Security group rules for the CCE nodes:

| Direction | Protocol | Port | Source | Purpose |
|---|---|---|---|---|
| Egress | TCP | 443 | 0.0.0.0/0 | Vertex AI, SWR pulls |
| Egress | TCP | 5432 | 192.168.3.0/24 | RDS PostgreSQL |
| Egress | UDP | 53 | VPC CIDR | DNS |
| Ingress | TCP | 8080 | ELB subnet | App traffic from ELB |

### 4.2 CCE Cluster

Create a CCE cluster with at least 2 nodes in different AZs:

```
Console → CCE → Create Cluster
  Cluster type:   CCE Standard Cluster
  K8s version:    v1.29 (latest stable)
  VPC:            your-vpc
  Master nodes:   3 (for HA; 1 is fine for non-critical environments)
  Node pool:
    Node type:    c7.large.2 (2 vCPU / 4 GB) — minimum; use c7.xlarge.2 for heavier load
    OS:           HCE OS 2.0 (Huawei Cloud EulerOS — recommended)
    Node count:   2 (across 2 AZs)
    Root disk:    40 GB SSD
  Network plugin: Yangtse CNI (supports NetworkPolicy)
```

> **Important:** Choose the **Yangtse CNI** network plugin. It supports
> `NetworkPolicy` resources, which the `k8s/09-network-policy.yaml` manifests
> require. The older Neutron CNI does not enforce NetworkPolicies.

### 4.3 SWR Container Registry

```
Console → SWR → Create Organization → private-km
```

Note the login command for Docker:

```bash
# Get the temporary Docker login command from:
# Console → SWR → My Images → Generate Login Command
# It looks like:
docker login -u REGION@ACCESS_KEY -p TOKEN swr.REGION.myhuaweicloud.com
```

Set shell variables for the image address:

```bash
export SWR_REGION="ap-southeast-3"                  # ← your SWR region
export SWR_ORG="your-org"                           # ← your SWR organisation
export SWR_ADDR="swr.${SWR_REGION}.myhuaweicloud.com"
export IMAGE="${SWR_ADDR}/${SWR_ORG}/private-km"
```

### 4.4 RDS PostgreSQL

```
Console → RDS → Create DB Instance
  Engine:         PostgreSQL 16
  Type:           Primary/Standby (recommended for production HA)
  Specifications: rds.pg.c6.medium.4 (2 vCPU / 8 GB) — adjust to load
  Storage:        100 GB SSD (with auto-expand enabled)
  VPC/Subnet:     your-vpc / subnet-db
  Security group: allow port 5432 from CCE node subnet only
  Backup:         Enable automated backups, 7-day retention
```

After the instance is created, note the **Private IP address**. Then create
the database and user:

```bash
# Connect via the Huawei DAS (Data Admin Service) web console, or:
# Console → RDS → Your Instance → Log In → SQL Window

CREATE DATABASE private_km;
CREATE USER private_km_user WITH ENCRYPTED PASSWORD 'STRONG_PASSWORD_HERE';
GRANT ALL PRIVILEGES ON DATABASE private_km TO private_km_user;
\c private_km
GRANT ALL ON SCHEMA public TO private_km_user;

-- Schema (same as in DEPLOYMENT.md Cloud SQL section)
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
```

---

## 5. GCP Service Account for Vertex AI

Because the application is no longer running on GCP infrastructure, it cannot
use Workload Identity. Instead, it authenticates to the Vertex AI API using a
**GCP service account JSON key**, which is injected as a Kubernetes Secret and
mounted into the container at `/var/secrets/gcp/key.json`.

The `lib/gemini.ts` code reads `GOOGLE_APPLICATION_CREDENTIALS` and uses it
automatically — no code changes needed.

### 5.1 Create the service account in GCP

```bash
# Run these in your GCP project (not on Huawei)
export GCP_PROJECT="your-gcp-project-id"
export SA_NAME="private-km-vertex"
export SA_EMAIL="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"

gcloud iam service-accounts create $SA_NAME \
  --project=$GCP_PROJECT \
  --display-name="Private KM Vertex AI client (Huawei Cloud)"

# Grant only the Vertex AI User role
gcloud projects add-iam-policy-binding $GCP_PROJECT \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/aiplatform.user"
```

### 5.2 Export the JSON key

```bash
gcloud iam service-accounts keys create ./gcp-sa-key.json \
  --iam-account=$SA_EMAIL \
  --project=$GCP_PROJECT
```

> **Security note:** This JSON file contains a long-lived credential. Treat it
> like a password:
> - Never commit it to git (it is listed in `.gitignore`)
> - Store it in your secret vault and rotate it annually
> - In production, prefer short-lived tokens via Workload Identity Federation
>   between Huawei Cloud and GCP (see §5.3)

### 5.3 (Advanced) Workload Identity Federation — no key file

For the highest security posture, you can configure GCP Workload Identity
Federation to trust Huawei CCE's OIDC tokens, eliminating the long-lived JSON
key entirely. This is the recommended approach for production.

```bash
# Create a Workload Identity Pool in GCP
gcloud iam workload-identity-pools create huawei-cce-pool \
  --project=$GCP_PROJECT \
  --location=global \
  --display-name="Huawei CCE Pool"

# Add the CCE OIDC provider
# Replace OIDC_ISSUER_URL with your CCE cluster's OIDC issuer URL
# (found in: CCE Console → Cluster Details → API Server → OIDC Issuer)
gcloud iam workload-identity-pools providers create-oidc huawei-cce-provider \
  --project=$GCP_PROJECT \
  --location=global \
  --workload-identity-pool=huawei-cce-pool \
  --issuer-uri="OIDC_ISSUER_URL" \
  --allowed-audiences="https://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/huawei-cce-pool/providers/huawei-cce-provider"

# Grant the pool the Vertex AI User role
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --project=$GCP_PROJECT \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/huawei-cce-pool/*"
```

When using Workload Identity Federation, replace the `gcp-service-account`
secret with a credential configuration file generated by `gcloud iam
workload-identity-pools create-cred-config`.

---

## 6. Build and Push the Production Image

Use `Dockerfile.production` (not `Dockerfile`) — it swaps the SQLite adapter
for the PostgreSQL one automatically.

```bash
# From the project root
TAG=$(git rev-parse --short HEAD)   # use git SHA as image tag

# Log in to SWR
docker login -u ${SWR_REGION}@YOUR_ACCESS_KEY -p YOUR_TOKEN ${SWR_ADDR}

# Build for linux/amd64 (required for CCE x86 nodes)
docker build \
  --platform linux/amd64 \
  --file Dockerfile.production \
  --tag ${IMAGE}:${TAG} \
  --tag ${IMAGE}:latest \
  .

# Push both tags
docker push ${IMAGE}:${TAG}
docker push ${IMAGE}:latest

echo "Image pushed: ${IMAGE}:${TAG}"
```

> **Apple Silicon (M1/M2/M3 Mac):** `--platform linux/amd64` is mandatory.
> Without it, Docker builds an ARM64 image that will not run on CCE x86 nodes.

---

## 7. Configure kubectl for CCE

```
Console → CCE → Your Cluster → Connection Info → kubectl
  → Download kubeconfig (internal network)  ← use this if CI/CD runs inside VPC
  → Download kubeconfig (public network)    ← use this for local development
```

```bash
# Save to ~/.kube/config or set KUBECONFIG
export KUBECONFIG=~/Downloads/kubeconfig.yaml

# Verify connection
kubectl get nodes
# NAME           STATUS   ROLES    AGE   VERSION
# cce-node-xxx   Ready    <none>   1d    v1.29.x
```

---

## 8. Create Kubernetes Secrets

Secrets are created with `kubectl create secret` — **never** apply the
`03-secret.example.yaml` file with real values or commit credentials to git.

### 8.1 App secrets (DB URL + JWT)

```bash
# Replace placeholders with real values
DB_PRIVATE_IP="192.168.3.XXX"   # RDS private IP from §4.4
DB_PASSWORD="STRONG_PASSWORD_HERE"

kubectl create secret generic app-secrets \
  --namespace=private-km \
  --from-literal=DATABASE_URL="postgresql://private_km_user:${DB_PASSWORD}@${DB_PRIVATE_IP}:5432/private_km" \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)"
```

### 8.2 GCP service account key

```bash
kubectl create secret generic gcp-service-account \
  --namespace=private-km \
  --from-file=key.json=./gcp-sa-key.json
```

### 8.3 SWR image pull secret

CCE needs credentials to pull your private SWR image:

```bash
kubectl create secret docker-registry swr-secret \
  --namespace=private-km \
  --docker-server=${SWR_ADDR} \
  --docker-username="${SWR_REGION}@YOUR_ACCESS_KEY" \
  --docker-password="YOUR_SWR_TOKEN"
```

### 8.4 Verify

```bash
kubectl get secrets -n private-km
# NAME                  TYPE                             DATA   AGE
# app-secrets           Opaque                           2      1m
# gcp-service-account   Opaque                           1      1m
# swr-secret            kubernetes.io/dockerconfigjson   1      1m
```

---

## 9. Update Manifests for Your Environment

Before applying, replace all placeholder values in the manifests:

### k8s/02-configmap.yaml

```bash
# Replace GCP project ID and Vertex AI region
sed -i \
  -e 's/YOUR_GCP_PROJECT_ID/your-actual-gcp-project/' \
  -e 's/us-central1/your-vertex-ai-region/' \
  k8s/02-configmap.yaml
```

### k8s/04-deployment.yaml

```bash
# Replace image address with your SWR image
sed -i \
  "s|swr.ap-southeast-3.myhuaweicloud.com/YOUR_ORG/private-km:latest|${IMAGE}:${TAG}|" \
  k8s/04-deployment.yaml
```

### k8s/06-ingress.yaml

```bash
# Replace domain and certificate ID
sed -i \
  -e 's/chat.yourcompany.com/chat.youractualcompany.com/g' \
  -e 's/REPLACE_WITH_SCM_CERT_ID/your-scm-cert-id/' \
  k8s/06-ingress.yaml
```

### k8s/09-network-policy.yaml

```bash
# Replace the RDS subnet CIDR with your actual RDS subnet
sed -i 's|192.168.0.0/24|192.168.3.0/24|' k8s/09-network-policy.yaml
```

### k8s/kustomization.yaml

```bash
sed -i "s|swr.ap-southeast-3.myhuaweicloud.com/YOUR_ORG/private-km|${IMAGE}|" k8s/kustomization.yaml
sed -i "s|newTag: latest|newTag: ${TAG}|" k8s/kustomization.yaml
```

---

## 10. Deploy to CCE

Apply all manifests in order using kustomize:

```bash
# Create the namespace first (must exist before other resources)
kubectl apply -f k8s/00-namespace.yaml

# Apply everything else
kubectl apply -k k8s/

# Watch pods come up
kubectl rollout status deployment/private-km -n private-km --timeout=120s
```

Expected output after a successful deployment:

```
deployment.apps/private-km successfully rolled out
```

Verify all resources:

```bash
kubectl get all -n private-km

# NAME                              READY   STATUS    RESTARTS   AGE
# pod/private-km-7d9f8c6b5-4xk2p   1/1     Running   0          2m
# pod/private-km-7d9f8c6b5-9mnrq   1/1     Running   0          2m
#
# NAME                 TYPE        CLUSTER-IP      PORT(S)   AGE
# service/private-km   ClusterIP   10.247.xxx.xxx  80/TCP    2m
#
# NAME                         READY   UP-TO-DATE   AVAILABLE
# deployment.apps/private-km   2/2     2            2
#
# NAME                                        REFERENCE               MINPODSHELMAXPODS
# horizontalpodautoscaler.autoscaling/...     Deployment/private-km   2        10
```

---

## 11. TLS / Custom Domain

### 11.1 Upload your SSL certificate to Huawei SCM

```
Console → Cloud Certificate Manager (CCM) → SSL Certificates
  → Upload Certificate
    Paste your certificate PEM and private key
    Note the Certificate ID
```

### 11.2 Update the Ingress

Set `kubernetes.io/elb.cert-id` in `k8s/06-ingress.yaml` to the certificate
ID from CCM, then re-apply:

```bash
kubectl apply -f k8s/06-ingress.yaml
```

### 11.3 Get the ELB public IP

```bash
kubectl get ingress private-km -n private-km
# NAME         CLASS   HOSTS                  ADDRESS          PORTS     AGE
# private-km   <none>  chat.yourcompany.com   119.xxx.xxx.xxx  80, 443   5m
```

Add a DNS `A` record for `chat.yourcompany.com` pointing to the ELB public IP.

---

## 12. Verify the Deployment

```bash
# 1. Health probe responds
kubectl exec -n private-km \
  $(kubectl get pod -n private-km -l app.kubernetes.io/name=private-km -o name | head -1) \
  -- wget -qO- http://localhost:8080/api/health
# {"status":"ok","timestamp":1234567890}

# 2. Pod logs look clean
kubectl logs -n private-km -l app.kubernetes.io/name=private-km --tail=30

# 3. Check HPA is reading metrics
kubectl get hpa -n private-km
# NAME         REFERENCE               TARGETS          MINPODS   MAXPODS   REPLICAS
# private-km   Deployment/private-km   12%/60%, 30%/75%   2         10        2

# 4. End-to-end: open https://chat.yourcompany.com in a browser
#    Register an account, send a message, confirm a Gemini response streams back
```

---

## 13. Rotating Secrets

When a secret changes (DB password rotation, JWT key rotation, GCP SA key
rotation), update the Kubernetes Secret and trigger a rolling restart:

```bash
# Example: rotate JWT_SECRET
kubectl create secret generic app-secrets \
  --namespace=private-km \
  --from-literal=DATABASE_URL="$(kubectl get secret app-secrets -n private-km -o jsonpath='{.data.DATABASE_URL}' | base64 -d)" \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)" \
  --dry-run=client -o yaml | kubectl apply -f -

# Rolling restart to pick up the new secret value
kubectl rollout restart deployment/private-km -n private-km
kubectl rollout status  deployment/private-km -n private-km
```

> **Note:** After rotating `JWT_SECRET`, all active browser sessions will be
> invalidated and users will be asked to log in again.

---

## 14. Upgrading the Application

Standard production upgrade procedure (zero downtime):

```bash
# 1. Build and push the new image
TAG=$(git rev-parse --short HEAD)
docker build --platform linux/amd64 --file Dockerfile.production \
  --tag ${IMAGE}:${TAG} --tag ${IMAGE}:latest .
docker push ${IMAGE}:${TAG}
docker push ${IMAGE}:latest

# 2. Update the image tag in kustomization.yaml
sed -i "s|newTag: .*|newTag: ${TAG}|" k8s/kustomization.yaml

# 3. Apply — Kubernetes will roll out one pod at a time
kubectl apply -k k8s/

# 4. Monitor the rollout
kubectl rollout status deployment/private-km -n private-km

# 5. If something goes wrong, roll back immediately
kubectl rollout undo deployment/private-km -n private-km
kubectl rollout status deployment/private-km -n private-km
```

The `maxUnavailable: 0` rolling update strategy guarantees that traffic is
never interrupted during upgrades.

---

## 15. Scaling

### Manual scale (during a known traffic event)

```bash
kubectl scale deployment/private-km --replicas=5 -n private-km
```

### Adjust HPA bounds

```bash
kubectl patch hpa private-km -n private-km \
  --patch '{"spec":{"minReplicas":3,"maxReplicas":20}}'
```

### Vertical scaling (bigger nodes)

```
Console → CCE → Node Pools → Add Node Pool
  Add larger nodes (e.g. c7.xlarge.4) and taint old nodes to drain gracefully
```

---

## 16. Monitoring and Observability

### Built-in CCE monitoring

```
Console → CCE → Your Cluster → Monitoring
  → View CPU / memory utilisation per pod
  → Set alerts for pod restart count > 3
```

### Application Metrics (AOM — Application Operations Management)

Huawei AOM collects container logs and metrics automatically for CCE clusters.

```
Console → AOM → Log Management → Log Search
  Filter by: namespace=private-km
```

### Kubernetes event watch (quick triage)

```bash
# Watch all events in the namespace
kubectl get events -n private-km --sort-by='.lastTimestamp' -w

# Describe a pod that is not starting
kubectl describe pod -n private-km $(kubectl get pod -n private-km -l app.kubernetes.io/name=private-km -o name | head -1)
```

### RDS monitoring

```
Console → RDS → Your Instance → Monitoring
  → Watch: connections, CPU, storage, slow query count
  → Set alert: connections > 80% of max_connections
```

---

## 17. Cost Considerations

Approximate monthly costs (Asia-Pacific region, production HA configuration):

| Service | Config | Est. Monthly Cost |
|---|---|---|
| CCE cluster control plane | Standard, 3 masters | ~$70–100 |
| CCE nodes (×2) | c7.large.2 (2C/4G) | ~$80–120 |
| RDS PostgreSQL (Primary/Standby) | rds.pg.c6.medium.4 | ~$120–160 |
| ELB | shared, 5 Mbit/s | ~$10–20 |
| SWR storage | ~2 GB images | ~$1 |
| EIP (public IP for ELB) | traffic-based | ~$5–15 |
| **Total infrastructure** | | **~$290–420/month** |
| Vertex AI (Gemini 1.5 Pro) | depends on usage | variable |

**Cost-saving tips:**
- Use `c7.small.2` (1C/2G) nodes in non-prod environments
- Use `db-smallest` RDS tier for non-prod
- Enable CCE node auto-scaling to scale down during nights/weekends
- Switch to `gemini-1.5-flash` in `GEMINI_MODEL` for 5× lower token costs

---

## 18. Troubleshooting

### Pods are CrashLooping

```bash
kubectl logs -n private-km \
  $(kubectl get pod -n private-km -l app.kubernetes.io/name=private-km -o name | head -1) \
  --previous

kubectl describe pod -n private-km \
  $(kubectl get pod -n private-km -l app.kubernetes.io/name=private-km -o name | head -1)
```

Common causes:
- **`DATABASE_URL` not set or wrong** → verify secret: `kubectl get secret app-secrets -n private-km -o jsonpath='{.data.DATABASE_URL}' | base64 -d`
- **`GOOGLE_APPLICATION_CREDENTIALS` file missing** → verify: `kubectl get secret gcp-service-account -n private-km`
- **Port not 8080** → confirm `PORT=8080` in configmap

### Cannot connect to RDS

```bash
# Test connectivity from inside a pod
kubectl exec -n private-km \
  $(kubectl get pod -n private-km -l app.kubernetes.io/name=private-km -o name | head -1) \
  -- nc -zv RDS_PRIVATE_IP 5432
```

If the connection times out:
1. Check RDS security group — allow port 5432 from the CCE node subnet
2. Check the NetworkPolicy `allow-rds-postgres` — update the CIDR to match your RDS subnet
3. Verify the VPC peering (if RDS is in a different VPC)

### Vertex AI authentication fails

Symptoms: `Error: Could not load the default credentials`

```bash
# Verify the key.json is mounted correctly
kubectl exec -n private-km \
  $(kubectl get pod -n private-km -l app.kubernetes.io/name=private-km -o name | head -1) \
  -- cat /var/secrets/gcp/key.json | python3 -m json.tool | head -5
```

If the file is missing or malformed, recreate the secret:
```bash
kubectl delete secret gcp-service-account -n private-km
kubectl create secret generic gcp-service-account \
  --namespace=private-km \
  --from-file=key.json=./gcp-sa-key.json
kubectl rollout restart deployment/private-km -n private-km
```

### AI responses are cut off mid-stream

The default request timeout on the ELB Ingress is 60 s. Long AI responses
can exceed this. Increase the timeout via the ingress annotation and re-apply:

```yaml
# k8s/06-ingress.yaml
annotations:
  kubernetes.io/elb.idle-timeout: "180"
  kubernetes.io/elb.request-timeout: "180"
  kubernetes.io/elb.response-timeout: "180"
```

```bash
kubectl apply -f k8s/06-ingress.yaml
```

### Image pull fails (ErrImagePull)

```bash
kubectl describe pod -n private-km POD_NAME | grep -A5 "Warning"
```

If the error is `unauthorized`, the SWR pull secret may have expired:
```bash
# Regenerate an SWR login token in the console, then:
kubectl delete secret swr-secret -n private-km
kubectl create secret docker-registry swr-secret \
  --namespace=private-km \
  --docker-server=${SWR_ADDR} \
  --docker-username="${SWR_REGION}@YOUR_ACCESS_KEY" \
  --docker-password="NEW_TOKEN"
kubectl rollout restart deployment/private-km -n private-km
```

### HPA shows `<unknown>` for metrics

The metrics-server must be running in the cluster:

```bash
kubectl get pods -n kube-system | grep metrics-server
```

If missing, install it (CCE usually includes it by default):
```
Console → CCE → Add-ons → Metrics Server → Install
```
