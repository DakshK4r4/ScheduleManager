# ScheduleManager Production Deployment Runbook

**Audience:** Site Reliability Engineers, DevOps Operators  
**Estimated Deployment Duration:** ~25 minutes  
**Target Environment:** Linux VPS (Ubuntu 22.04 / 24.04 LTS)  

---

## 1. Prerequisites & Host Provisioning

### Minimum Recommended Virtual Machine:
- **Provider:** Hetzner Cloud (CPX31), DigitalOcean (Basic 8GB), AWS Lightsail (8GB), or similar.
- **vCPU:** 4 cores
- **RAM:** 8 GB
- **Storage:** 50 GB NVMe / SSD
- **Operating System:** Ubuntu 24.04 LTS (x86_64)
- **Static IP:** 1 public IPv4 address assigned to host.
- **DNS Records:**
  - `A` record: `schedule.yourcompany.com` $\rightarrow$ Host Public IPv4.

### Host System Preparation:
Execute on the clean server via SSH:
```bash
# 1. Update OS packages
sudo apt update && sudo apt upgrade -y

# 2. Install essential system tools
sudo apt install -y curl git ufw fail2ban rsync unattended-upgrades

# 3. Install Docker Engine and Docker Compose Plugin
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# 4. Configure basic host firewall
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 80/tcp comment 'HTTP Let’s Encrypt'
sudo ufw allow 443/tcp comment 'HTTPS'
sudo ufw enable
```

---

## 2. Directory Structure & Code Deployment

Create application root on the host:
```bash
sudo mkdir -p /opt/schedulemanager
sudo chown -R $USER:$USER /opt/schedulemanager
cd /opt/schedulemanager

# Clone repository
git clone https://github.com/your-org/ScheduleManager.git .
```

Create host data directories for persistent storage:
```bash
mkdir -p /opt/schedulemanager/data/postgres
mkdir -p /opt/schedulemanager/data/minio
mkdir -p /opt/schedulemanager/caddy_data
```

---

## 3. Production Environment Configuration

Create `/opt/schedulemanager/.env` with production permissions:
```bash
touch /opt/schedulemanager/.env
chmod 600 /opt/schedulemanager/.env
```

Populate `/opt/schedulemanager/.env` (generate strong passwords using `openssl rand -hex 24`):
```ini
# ==============================================================================
# Environment Identification
# ==============================================================================
ENVIRONMENT=production
NODE_ENV=production

# ==============================================================================
# Domain & Networking
# ==============================================================================
DOMAIN_NAME=schedule.yourcompany.com
NEXT_PUBLIC_API_URL=https://schedule.yourcompany.com
BACKEND_INTERNAL_URL=http://backend:8000

# ==============================================================================
# PostgreSQL Database Credentials
# ==============================================================================
POSTGRES_USER=primavera_admin
POSTGRES_PASSWORD=GENERATE_STRONG_RANDOM_PASSWORD_HERE
POSTGRES_DB=primavera
DATABASE_URL=postgresql+psycopg2://primavera_admin:GENERATE_STRONG_RANDOM_PASSWORD_HERE@postgres:5432/primavera

# ==============================================================================
# MinIO Object Storage Credentials
# ==============================================================================
MINIO_ROOT_USER=minio_admin
MINIO_ROOT_PASSWORD=GENERATE_STRONG_RANDOM_PASSWORD_HERE
MINIO_BUCKET=sih-artifacts
MINIO_ENDPOINT=minio:9000
MINIO_SECURE=false

# ==============================================================================
# External AI API Credentials (Isolated per service)
# ==============================================================================
TIME_AGENT_GEMINI_API_KEY=YOUR_TIME_AGENT_GEMINI_KEY
TIME_AGENT_LLM_MODEL=gemini-2.5-flash

EXTRACTION_GEMINI_API_KEY=YOUR_EXTRACTION_GEMINI_KEY
EXTRACTION_LLM_MODEL=gemini-3.5-flash

SARVAM_API_KEY=YOUR_SARVAM_API_KEY

ALLOW_LEGACY_GEMINI_FALLBACK=false
```

---

## 4. Production Docker Compose Configuration

Create `/opt/schedulemanager/docker-compose.prod.yml` (omitting development bind mounts and closing host database ports):

```yaml
version: '3.8'

services:
  caddy:
    image: caddy:2-alpine
    container_name: schedulemanager-caddy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./caddy_data:/data
    networks:
      - internal-net

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
      args:
        NEXT_PUBLIC_API_URL: https://${DOMAIN_NAME}
    container_name: schedulemanager-frontend
    restart: unless-stopped
    environment:
      - NODE_ENV=production
      - PORT=3000
      - BACKEND_INTERNAL_URL=http://backend:8000
    networks:
      - internal-net

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: schedulemanager-backend
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      postgres:
        condition: service_healthy
      minio:
        condition: service_started
      document-parser:
        condition: service_started
    networks:
      - internal-net

  document-parser:
    build:
      context: ./document-parser
      dockerfile: Dockerfile
    container_name: schedulemanager-document-parser
    restart: unless-stopped
    environment:
      - PORT=8001
    networks:
      - internal-net

  postgres:
    image: postgres:16-alpine
    container_name: schedulemanager-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - internal-net

  minio:
    image: quay.io/minio/minio:latest
    container_name: schedulemanager-minio
    restart: unless-stopped
    command: server /data
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - ./data/minio:/data
    networks:
      - internal-net

networks:
  internal-net:
    driver: bridge
```

Create `/opt/schedulemanager/Caddyfile`:
```caddy
{$DOMAIN_NAME} {
    encode gzip zstd

    # Route API and downloads directly to backend
    handle_path /api/v1/* {
        reverse_proxy backend:8000
    }

    # Route all other traffic to frontend Next.js server
    handle {
        reverse_proxy frontend:3000
    }
}
```

---

## 5. Build & Service Launch

```bash
# 1. Build all immutable application images
docker compose -f docker-compose.prod.yml build --no-cache

# 2. Start services in background
docker compose -f docker-compose.prod.yml up -d

# 3. Inspect container status
docker compose -f docker-compose.prod.yml ps
```

All containers should report `Up` or `Up (healthy)`.

---

## 6. Verification and Health Checks

Execute the following smoke tests against the running production instance:

```bash
# 1. Verify Caddy ingress and TLS
curl -I https://schedule.yourcompany.com

# 2. Verify backend internal health check
docker exec schedulemanager-backend curl -f http://localhost:8000/health

# 3. Verify document parser internal health check
docker exec schedulemanager-document-parser curl -f http://localhost:8001/health

# 4. Verify PostgreSQL database connection
docker exec schedulemanager-postgres pg_isready -U primavera_admin -d primavera

# 5. Verify MinIO health
docker exec schedulemanager-backend python -c "
from app.services.minio_service import minio_service
assert minio_service.is_minio_connected(), 'MinIO connection failed!'
print('MinIO storage check: OK')
"
```

---

## 7. Automated Backup Setup (Cron)

Create an automated backup script `/opt/schedulemanager/scripts/backup.sh`:
```bash
#!/bin/bash
set -e
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="/opt/schedulemanager/backups"
mkdir -p "$BACKUP_DIR"

# 1. Dump PostgreSQL database
docker exec schedulemanager-postgres pg_dump -U primavera_admin primavera | gzip > "$BACKUP_DIR/db_$TIMESTAMP.sql.gz"

# 2. Retain last 7 days locally
find "$BACKUP_DIR" -type f -name "db_*.sql.gz" -mtime +7 -delete

echo "Backup completed: db_$TIMESTAMP.sql.gz"
```
Make executable and schedule in crontab:
```bash
chmod +x /opt/schedulemanager/scripts/backup.sh
(crontab -l 2>/dev/null; echo "0 2 * * * /opt/schedulemanager/scripts/backup.sh >> /var/log/schedulemanager_backup.log 2>&1") | crontab -
```

---

## 8. Rollback Procedure

If a deployed version causes runtime failure:
```bash
# 1. Stop current containers
docker compose -f docker-compose.prod.yml down

# 2. Check out previous stable git commit / tag
git checkout <PREVIOUS_STABLE_COMMIT_HASH>

# 3. Rebuild and launch previous images
docker compose -f docker-compose.prod.yml up --build -d

# 4. If database schema was altered, restore from backup:
# gunzip -c /opt/schedulemanager/backups/db_<TIMESTAMP>.sql.gz | docker exec -i schedulemanager-postgres psql -U primavera_admin primavera
```
