#!/usr/bin/env python3
"""在 GitHub Actions 中自动创建/更新 Render Web Service 并获取访问链接"""
import os
import sys
import time
import json
import requests

RENDER_API_KEY = os.environ.get("RENDER_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # provided by Actions
REPO = os.environ.get("GITHUB_REPOSITORY", "shangdabaitu/page-replica-v2")
OWNER, REPO_NAME = REPO.split("/")
SERVICE_NAME = "page-replica-v2"

HEADERS = {
    "Authorization": f"Bearer {RENDER_API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def get_user_id():
    resp = requests.get("https://api.render.com/v1/users", headers=HEADERS, timeout=30)
    print("users response status:", resp.status_code)
    print("users response body:", resp.text[:500])
    resp.raise_for_status()
    users = resp.json()
    # Render may return a list or a dict under a key
    if isinstance(users, dict):
        if "user" in users:
            return users["user"]["id"]
        if "id" in users:
            return users["id"]
        raise RuntimeError(f"Unexpected users response shape: {users.keys()}")
    if not users:
        raise RuntimeError("No Render users found")
    return users[0]["id"]


def find_service():
    resp = requests.get("https://api.render.com/v1/services?limit=20", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    for svc in resp.json():
        if svc.get("name") == SERVICE_NAME:
            return svc
    return None


def create_service(owner_id: str):
    payload = {
        "type": "web_service",
        "name": SERVICE_NAME,
        "ownerId": owner_id,
        "repo": f"https://github.com/{REPO}",
        "branch": "master",
        "runtime": "python",
        "buildCommand": "pip install -r requirements.txt",
        "startCommand": "python api/replicate.py",
        "plan": "free",
        "region": "oregon",
        "autoDeploy": "yes",
        "envVars": [
            {"key": "PYTHON_VERSION", "value": "3.10"},
        ],
    }
    resp = requests.post("https://api.render.com/v1/services", headers=HEADERS, json=payload, timeout=60)
    if resp.status_code != 201:
        print("Create service failed:", resp.status_code, resp.text)
        sys.exit(1)
    return resp.json()


def trigger_deploy(service_id: str):
    resp = requests.post(f"https://api.render.com/v1/services/{service_id}/deploys", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def wait_for_service_url(service_id: str, timeout: int = 300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"https://api.render.com/v1/services/{service_id}", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        svc = resp.json()
        url = svc.get("serviceDetails", {}).get("url") or svc.get("url")
        status = svc.get("serviceDetails", {}).get("status") or svc.get("status")
        print(f"service status: {status}, url: {url}")
        if url and status in ("live", "degraded", "update_in_progress"):
            return url
        time.sleep(10)
    raise RuntimeError("Timeout waiting for service URL")


def set_github_variable(name: str, value: str):
    if not GITHUB_TOKEN:
        print(f"GITHUB_TOKEN not available, skip setting {name}")
        return
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    url = f"https://api.github.com/repos/{REPO}/actions/variables"
    payload = {"name": name, "value": value}
    # try create
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code == 409:
        r = requests.patch(f"{url}/{name}", headers=headers, json={"value": value}, timeout=30)
    print(f"set {name} status: {r.status_code}")


def main():
    if not RENDER_API_KEY:
        print("RENDER_API_KEY missing")
        sys.exit(1)

    owner_id = get_user_id()
    print(f"ownerId: {owner_id}")

    svc = find_service()
    if svc:
        print(f"Service exists: {svc['id']}")
        service_id = svc["id"]
        trigger_deploy(service_id)
    else:
        print("Creating service...")
        svc = create_service(owner_id)
        service_id = svc["id"]
        print(f"Created service: {service_id}")

    url = wait_for_service_url(service_id)
    print(f"Render URL: {url}")
    set_github_variable("RENDER_URL", url)
    # Also write to a file for easy access in Actions logs
    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
        f.write(f"url={url}\n")


if __name__ == "__main__":
    main()
