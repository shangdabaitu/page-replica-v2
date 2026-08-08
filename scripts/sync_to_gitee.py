#!/usr/bin/env python3
"""把 docs 目录同步到 Gitee 仓库根目录，供 Gitee Pages 部署"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GITEE_TOKEN = os.environ.get("GITEE_TOKEN", "")
GITEE_USER = os.environ.get("GITEE_USER", "")
GITEE_REPO = os.environ.get("GITEE_REPO", "page-replica-v2")
SOURCE_DIR = Path(os.environ.get("SOURCE_DIR", "docs")).resolve()


def run(cmd, cwd=None, check=True):
    print(f"[run] {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")
    return result


def main():
    if not GITEE_TOKEN:
        print("::error::GITEE_TOKEN missing")
        sys.exit(1)
    if not GITEE_USER:
        print("::error::GITEE_USER missing")
        sys.exit(1)
    if not SOURCE_DIR.exists():
        print(f"::error::SOURCE_DIR not found: {SOURCE_DIR}")
        sys.exit(1)

    repo_url = f"https://{GITEE_USER}:{GITEE_TOKEN}@gitee.com/{GITEE_USER}/{GITEE_REPO}.git"

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "gitee-sync"
        work_dir.mkdir()

        # 初始化一个干净的仓库
        run("git init", cwd=work_dir)
        run("git config user.email 'bot@example.com'", cwd=work_dir)
        run("git config user.name 'Trae Bot'", cwd=work_dir)
        run("git remote add origin " + repo_url, cwd=work_dir)

        # 拉取远程分支（如果存在），避免非 fast-forward 推送失败
        fetch_result = run("git fetch origin master", cwd=work_dir, check=False)
        if fetch_result.returncode == 0:
            run("git reset --soft origin/master", cwd=work_dir)
        else:
            print("Remote master not found, will create a new one")

        # 清空工作目录（保留 .git）
        for item in work_dir.iterdir():
            if item.name == ".git":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        # 复制 docs 内容到仓库根目录
        for item in SOURCE_DIR.iterdir():
            dest = work_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        # 提交并推送
        run("git add -A", cwd=work_dir)
        run("git commit -m 'Sync docs to Gitee Pages from GitHub Actions'", cwd=work_dir, check=False)
        run("git push -u origin master --force", cwd=work_dir)

    print(f"::notice::Synced {SOURCE_DIR} to https://gitee.com/{GITEE_USER}/{GITEE_REPO}")
    print(f"::notice::After enabling Gitee Pages, visit https://{GITEE_USER}.gitee.io/{GITEE_REPO}")


if __name__ == "__main__":
    main()
