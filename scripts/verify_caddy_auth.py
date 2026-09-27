import subprocess
import time
import urllib.request
import urllib.error
import base64
import os

def test_caddy_basic_auth():
    # Password: "ProductionSecretPassword123!"
    password = "ProductionSecretPassword123!"
    user = "admin"
    
    # Generate hash using Caddy
    p_hash = subprocess.run(
        ["docker", "run", "--rm", "caddy:2-alpine", "caddy", "hash-password", "--plaintext", password],
        capture_output=True,
        text=True,
        check=True
    )
    raw_hash = p_hash.stdout.strip()
    b64_hash = base64.b64encode(raw_hash.encode("utf-8")).decode("ascii")
    print(f"[TEST] Raw hash: {raw_hash}")
    print(f"[TEST] Base64 hash: {b64_hash}")
    
    # Create test Caddyfile
    caddyfile_content = f""":80 {{
    basic_auth {{
        {user} {b64_hash}
    }}
    respond /health "HEALTH_OK" 200
    respond /api/test "API_OK" 200
    respond "FRONTEND_OK" 200
}}
"""
    test_caddyfile_path = os.path.abspath("test_caddyfile_auth")
    with open(test_caddyfile_path, "w", encoding="utf-8") as f:
        f.write(caddyfile_content)
        
    # Start temporary Caddy container on port 18080
    container_name = "test-caddy-auth-verify"
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    
    run_cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-p", "18080:80",
        "-v", f"{test_caddyfile_path}:/etc/caddy/Caddyfile:ro",
        "caddy:2-alpine"
    ]
    subprocess.run(run_cmd, check=True)
    time.sleep(2)
    
    try:
        # 1. Test unauthenticated request to / -> expect 401
        try:
            urllib.request.urlopen("http://localhost:18080/")
            raise AssertionError("Unauthenticated request succeeded, expected 401!")
        except urllib.error.HTTPError as e:
            assert e.code == 401, f"Expected 401, got {e.code}"
            print("[TEST] 1. Unauthenticated request correctly rejected with 401 Unauthorized")
            
        # 2. Test unauthenticated request to /api/test -> expect 401
        try:
            urllib.request.urlopen("http://localhost:18080/api/test")
            raise AssertionError("Unauthenticated API request succeeded, expected 401!")
        except urllib.error.HTTPError as e:
            assert e.code == 401, f"Expected 401, got {e.code}"
            print("[TEST] 2. Unauthenticated API request correctly rejected with 401 Unauthorized")

        # 3. Test wrong password -> expect 401
        wrong_auth = "Basic " + base64.b64encode(f"{user}:WrongPassword".encode("utf-8")).decode("ascii")
        try:
            req = urllib.request.Request("http://localhost:18080/api/test", headers={"Authorization": wrong_auth})
            urllib.request.urlopen(req)
            raise AssertionError("Wrong password succeeded, expected 401!")
        except urllib.error.HTTPError as e:
            assert e.code == 401, f"Expected 401, got {e.code}"
            print("[TEST] 3. Wrong password correctly rejected with 401 Unauthorized")

        # 4. Test correct credentials -> expect 200
        correct_auth = "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        req = urllib.request.Request("http://localhost:18080/api/test", headers={"Authorization": correct_auth})
        with urllib.request.urlopen(req) as resp:
            assert resp.getcode() == 200
            body = resp.read().decode("utf-8")
            assert body == "API_OK"
            print(f"[TEST] 4. Correct credentials authenticated successfully! (HTTP 200, Body: {body})")
            
        # 5. Test frontend route with correct credentials -> expect 200
        req_fe = urllib.request.Request("http://localhost:18080/", headers={"Authorization": correct_auth})
        with urllib.request.urlopen(req_fe) as resp:
            assert resp.getcode() == 200
            body = resp.read().decode("utf-8")
            assert body == "FRONTEND_OK"
            print(f"[TEST] 5. Frontend route authenticated successfully! (HTTP 200, Body: {body})")

        print("[TEST] ALL CADDY BASIC AUTH TESTS PASSED!")
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        if os.path.exists(test_caddyfile_path):
            os.remove(test_caddyfile_path)

if __name__ == "__main__":
    test_caddy_basic_auth()
