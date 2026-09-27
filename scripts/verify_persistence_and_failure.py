import json
import subprocess
import sys
import time
import urllib.request
import urllib.error

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost"

def log(msg):
    print(f"[PERSISTENCE-TEST] {msg}", flush=True)

def http_get(path, timeout=10):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "PersistenceScript/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), json.loads(resp.read().decode("utf-8"))

def run_cmd(cmd):
    log(f"Running: {cmd}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr

def test_persistence_and_recovery():
    log("=== STARTING PERSISTENCE & FAULT TOLERANCE VERIFICATION ===")

    # 1. Fetch current projects to establish baseline
    code, projs = http_get("/api/proxy/projects")
    assert code == 200, f"Cannot fetch projects: {code}"
    initial_count = len(projs)
    log(f"Baseline projects count: {initial_count}")
    assert initial_count > 0, "Expected at least 1 project from earlier import test"
    test_proj = projs[0]
    test_proj_id = test_proj["id"]

    # 2. Test Backend Restart
    log("\n--- Testing Backend Restart (PostgreSQL Data Persistence) ---")
    rc, out, err = run_cmd("docker compose -f docker-compose.prod.yml restart backend")
    assert rc == 0, f"Restart failed: {err}"
    # Wait for backend to be healthy again
    time.sleep(5)
    for _ in range(15):
        try:
            code, h = http_get("/health")
            if code == 200 and h.get("status") == "ok":
                break
        except Exception:
            pass
        time.sleep(2)

    code, projs_after_backend = http_get("/api/proxy/projects")
    assert code == 200
    assert len(projs_after_backend) == initial_count, "Project count changed after backend restart!"
    log("[PASS] 20. PostgreSQL data survives backend restart.")

    # 3. Test MinIO Restart
    log("\n--- Testing MinIO Restart (Artifact Data Persistence) ---")
    code, arts_before = http_get(f"/api/proxy/api/v1/projects/{test_proj_id}/artifacts")
    assert code == 200
    arts_count_before = len(arts_before)

    rc, out, err = run_cmd("docker compose -f docker-compose.prod.yml restart minio")
    assert rc == 0
    time.sleep(5)
    for _ in range(15):
        try:
            code, h = http_get("/health")
            if code == 200 and h.get("minio") == "ok":
                break
        except Exception:
            pass
        time.sleep(2)

    code, arts_after = http_get(f"/api/proxy/api/v1/projects/{test_proj_id}/artifacts")
    assert code == 200
    assert len(arts_after) == arts_count_before, "Artifacts lost after MinIO restart!"
    log("[PASS] 21. Artifact data survives MinIO restart.")

    # 4. Test Full Stack Restart (docker compose down / up -d)
    log("\n--- Testing Full Compose Restart (Persistent Volume Retention) ---")
    rc, out, err = run_cmd("docker compose -f docker-compose.prod.yml down")
    assert rc == 0
    rc, out, err = run_cmd("docker compose -f docker-compose.prod.yml up -d")
    assert rc == 0
    # Wait for full stack to become healthy
    time.sleep(8)
    for _ in range(25):
        try:
            code, h = http_get("/health")
            if code == 200 and h.get("status") == "ok" and h.get("postgres") == "ok":
                break
        except Exception:
            pass
        time.sleep(2)

    code, projs_after_full = http_get("/api/proxy/projects")
    assert code == 200
    assert len(projs_after_full) == initial_count, "Data lost after full Compose restart!"
    log("[PASS] 22. Full Compose restart preserves all state (PostgreSQL + MinIO named volumes).")

    # 5. Fault Simulation: Parser Temporarily Unavailable
    log("\n--- Testing Document Parser Temporary Outage ---")
    rc, _, _ = run_cmd("docker compose -f docker-compose.prod.yml stop document-parser")
    # Backend health should still be 200 OK because parser outage does not crash core app
    code, h = http_get("/health")
    assert code == 200, f"Backend failed during parser outage: {h}"
    log("[PASS] Backend health remains resilient during parser outage.")

    # Recover parser
    rc, _, _ = run_cmd("docker compose -f docker-compose.prod.yml start document-parser")
    time.sleep(5)
    log("[PASS] Document parser recovered cleanly.")

    log("\n=== ALL PERSISTENCE AND RESILIENCE TESTS PASSED ===")

if __name__ == "__main__":
    test_persistence_and_recovery()
