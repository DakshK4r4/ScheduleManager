# Production Environment Variable Specification

**Document:** Environment Variables and Secret Classification  
**Status:** Canonical Reference  
**Applicability:** ScheduleManager Production & Staging Deployments  

---

## 1. Environment Variable Reference Table

| Variable Name | Service Scope | Secret? | Frontend / Browser Exposed? | Required? | Source / Provider | Safe Default / Format | Description & Impact |
| :--- | :--- | :---: | :---: | :---: | :--- | :--- | :--- |
| `ENVIRONMENT` | Backend | No | No | Yes | Host configuration | `production` | Governs legacy credential fallback and debug logging. |
| `NODE_ENV` | Frontend | No | Yes (Build time) | Yes | Host configuration | `production` | Optimizes Next.js bundle and enables production React builds. |
| `DOMAIN_NAME` | Caddy / Host | No | No | Yes | DNS Registrar | `schedule.yourcompany.com` | Fully qualified domain for TLS certificate generation. |
| `NEXT_PUBLIC_API_URL` | Frontend | No | **YES (Public)** | Yes | Host configuration | `https://schedule.yourcompany.com` | Public base URL used by browser fetch calls. |
| `BACKEND_INTERNAL_URL`| Frontend | No | No (Server only) | Yes | Docker network | `http://backend:8000` | Internal URL used by Next.js `/api/proxy` rewrites. |
| `POSTGRES_USER` | PostgreSQL, Backend | No | No | Yes | Database configuration | `primavera_admin` | PostgreSQL database user account. |
| `POSTGRES_PASSWORD` | PostgreSQL, Backend | **YES** | No | **YES** | SRE / Vault | *None (Random string)* | Password for PostgreSQL user. |
| `POSTGRES_DB` | PostgreSQL, Backend | No | No | Yes | Database configuration | `primavera` | PostgreSQL target database name. |
| `DATABASE_URL` | Backend | **YES** | No | **YES** | Constructed from credentials | `postgresql+psycopg2://USER:PASS@postgres:5432/DB` | SQLAlchemy connection string. |
| `PARSER_URL` | Backend | No | No | Yes | Docker network | `http://document-parser:8001` | URL for the document-parser microservice. |
| `MINIO_ENDPOINT` | Backend | No | No | Yes | Docker network | `minio:9000` (or `s3.amazonaws.com`) | S3 API endpoint for artifact storage. |
| `MINIO_ROOT_USER` | MinIO | No | No | Yes | Storage configuration | `minio_admin` | MinIO access key / admin user. |
| `MINIO_ROOT_PASSWORD`| MinIO | **YES** | No | **YES** | SRE / Vault | *None (Random string)* | MinIO secret key / admin password. |
| `MINIO_BUCKET` | Backend, MinIO | No | No | Yes | Storage configuration | `sih-artifacts` | S3 bucket name for evidence files. |
| `MINIO_SECURE` | Backend | No | No | Yes | Host configuration | `false` (in Docker) / `true` (AWS S3) | Whether to use HTTPS for S3 communication. |
| `TIME_AGENT_GEMINI_API_KEY` | Backend | **YES** | No | Yes | Google AI Studio | *None (Key string)* | Dedicated API key for Time Agent conversational interactions. |
| `TIME_AGENT_LLM_MODEL` | Backend | No | No | Yes | Google AI Studio | `gemini-2.5-flash` | Gemini model for schedule agent reasoning. |
| `EXTRACTION_GEMINI_API_KEY` | Backend | **YES** | No | Yes | Google AI Studio | *None (Key string)* | Dedicated API key for multimodal document and image extraction. |
| `EXTRACTION_LLM_MODEL` | Backend | No | No | Yes | Google AI Studio | `gemini-3.5-flash` | Gemini model for multimodal document extraction. |
| `SARVAM_API_KEY` | Backend | **YES** | No | Optional | Sarvam AI Dashboard | *None (Key string)* | Key for Saaras multilingual speech-to-text. |
| `ALLOW_LEGACY_GEMINI_FALLBACK` | Backend | No | No | Yes | Host configuration | `false` (Production) | Disables fallback to legacy shared GEMINI_API_KEY in production. |
| `CORS_ORIGINS` | Backend | No | No | Yes | Host configuration | `https://schedule.yourcompany.com` | Comma-separated list of allowed origins. |

---

## 2. Secrets Handling & Storage Rules

1. **Storage Location:** Production secrets must be stored in `/opt/schedulemanager/.env` with strict file permissions (`chmod 600`).
2. **Access Control:** Only the `root` user or the dedicated deployer user should have read access to the `.env` file.
3. **No Repository Commits:** The `.env` file is explicitly ignored in `.gitignore`. CI/CD pipelines must never commit `.env` into git history.
4. **No Console Logging:** `CredentialResolver` in `backend/app/services/credential_resolver.py` enforces redaction of credentials. Under no circumstances should secrets appear in application logs or standard error outputs.
