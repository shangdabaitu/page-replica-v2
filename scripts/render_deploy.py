#!/usr/bin/env python3
"""在 GitHub Actions 中自动创建/更新 Render Static Site 并获取访问链接"""
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


def get_owner_id():
    # /v1/owners 返回当前用户/团队列表，包含 owner id
    resp = requests.get("https://api.render.com/v1/owners?limit=20", headers=HEADERS, timeout=30)
    print("owners response status:", resp.status_code)
    print("owners response body:", resp.text[:500])
    resp.raise_for_status()
    owners = resp.json()
    if isinstance(owners, dict):
        owners = owners.get("owners", owners)
    if not owners:
        raise RuntimeError("No Render owners found")
    first = owners[0]
    if "owner" in first:
        return first["owner"]["id"]
    return first["id"]


def find_service():
    resp = requests.get("https://api.render.com/v1/services?limit=20", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    for svc in resp.json():
        if svc.get("name") == SERVICE_NAME:
            return svc
    return None


def delete_service(service_id: str):
    """删除已有服务（类型不匹配时需要先删除再重建）"""
    resp = requests.delete(f"https://api.render.com/v1/services/{service_id}", headers=HEADERS, timeout=60)
    print(f"delete service {service_id} status: {resp.status_code}")
    if resp.status_code not in (200, 202, 204):
        print("Delete service failed:", resp.status_code, resp.text)
        sys.exit(1)
    # 删除是异步的，给点时间让 Render 回收名称
    time.sleep(5)


def create_service(owner_id: str):
    payload = {
        "type": "static_site",
        "name": SERVICE_NAME,
        "ownerId": owner_id,
        "repo": f"https://github.com/{REPO}",
        "branch": "master",
        "autoDeploy": "yes",
        "serviceDetails": {
            "buildCommand": "",  # docs 目录已经是构建好的静态站点，无需构建
            "publishPath": "docs",
        },
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
        # Render 对 static_site 的 url/status 放在 serviceDetails 下
        details = svc.get("serviceDetails", {})
        url = details.get("url") or svc.get("url")
        status = details.get("status") or svc.get("status")
        print(f"service status: {status}, url: {url}")
        # static_site 首次部署耗时较长，只要分配了 URL 且状态不是失败/暂停，即可认为可用
        if url and status not in ("failed", "suspended", "unknown"):
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

    owner_id = get_owner_id()
    print(f"ownerId: {owner_id}")

    svc = find_service()
    if svc:
        service_id = svc["id"]
        svc_type = svc.get("type") or svc.get("service", {}).get("type")
        print(f"Service exists: {service_id}, type: {svc_type}")
        # 如果现有服务不是 static_site，必须删除后重建，Render API 不支持直接修改类型
        if svc_type != "static_site":
            print("Existing service is not a static site, deleting and recreating...")
            delete_service(service_id)
            print("Creating static site service...")
            svc = create_service(owner_id)
            service_id = svc["id"]
            print(f"Created service: {service_id}")
        else:
            print("Triggering deploy...")
            trigger_deploy(service_id)
    else:
        print("Creating static site service...")
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
