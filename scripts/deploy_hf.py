#!/usr/bin/env python3
"""把项目部署到 Hugging Face Spaces（Docker 模式）"""
import os
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

REPO_ID = "dabaitujump/page-replica-v2"
SDK = "docker"

ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ["HUGGINGFACE_TOKEN"]


def main():
    api = HfApi(token=TOKEN)

    # 先确认 token 对应的用户
    me = api.whoami()
    print("Authenticated as:", me)

    # 创建 Space（如果不存在）；失败直接抛异常看原因
    print(f"Creating space {REPO_ID} ...")
    result = api.create_repo(
        repo_id=REPO_ID,
        repo_type="space",
        space_sdk=SDK,
        private=False,
        exist_ok=True,
    )
    print("create_repo result:", result)

    # 把需要上传的文件复制到临时目录
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp)
        include = [
            "api",
            "config.py",
            "core",
            "storage",
            "web",
            "requirements.txt",
            "runtime.txt",
            "Dockerfile",
            "README.md",
            "output",
        ]
        for name in include:
            src = ROOT / name
            if src.exists():
                if src.is_dir():
                    shutil.copytree(src, dst / name)
                else:
                    shutil.copy2(src, dst / name)

        # 上传文件夹
        api.upload_folder(
            repo_id=REPO_ID,
            repo_type="space",
            folder_path=str(dst),
            path_in_repo="",
            commit_message="Deploy from GitHub Actions",
        )
        print("Upload complete")


if __name__ == "__main__":
    main()
