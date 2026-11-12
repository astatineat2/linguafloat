"""Create the approved repositories and publish reviewed assets using local credentials."""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import hashlib
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat import __version__

OWNER = "astatineat2"


def credential():
    value = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if value:
        return value
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    response = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                              text=True, capture_output=True, env=environment, timeout=20)
    values = dict(line.split("=", 1) for line in response.stdout.splitlines() if "=" in line)
    if not values.get("password"):
        raise SystemExit("GitHub 登录未完成。请使用 Git Credential Manager 或 gh auth login 登录；不要把令牌写入项目或发到聊天中。")
    return values["password"]


def request(client, method, path, **kwargs):
    for attempt in range(3):
        try:
            response = client.request(method, "https://api.github.com" + path, **kwargs)
            if method == "GET" and response.status_code >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            break
        except httpx.TransportError:
            if method != "GET" or attempt == 2:
                raise SystemExit(f"GitHub {method} 连接中断；请重试，脚本会核对现有草稿和附件。") from None
            time.sleep(2 ** attempt)
    if response.status_code not in (200, 201, 204):
        raise SystemExit(f"GitHub {method} {path} 返回 HTTP {response.status_code}；未输出凭据或完整响应。")
    return response.json() if response.content else {}


def ensure_repositories(client):
    # Development stays private until the owner explicitly requests otherwise.
    for name, private in (("linguafloat", True), ("linguafloat-releases", False)):
        path = f"/repos/{OWNER}/{name}"
        existing = client.get("https://api.github.com" + path)
        if existing.status_code == 404:
            info = request(client, "POST", "/user/repos", json={"name": name, "private": private,
                            "description": "LinguaFloat source code" if name == "linguafloat" else "LinguaFloat Windows downloads and corresponding source",
                            "auto_init": False})
        elif existing.status_code == 200:
            info = existing.json()
        else:
            raise SystemExit(f"无法检查 {name}：HTTP {existing.status_code}")
        if info["private"] != private:
            raise SystemExit(f"{name} 可见性与已批准方案不符；脚本不会擅自更改。")
        print(info["html_url"])


def put_document(client, name, content):
    path = f"/repos/{OWNER}/linguafloat-releases/contents/{name}"
    old = client.get("https://api.github.com" + path)
    body = {"message": f"Document LinguaFloat v{__version__}", "content": base64.b64encode(content).decode()}
    if old.status_code == 200:
        if old.json().get("sha") == hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest():
            return
        body["sha"] = old.json()["sha"]
    elif old.status_code != 404:
        raise SystemExit(f"无法检查公开文档：HTTP {old.status_code}")
    request(client, "PUT", path, json=body)


def documents(client):
    for path in (ROOT / "public-download").glob("*.md"):
        put_document(client, path.name, path.read_bytes())
    put_document(client, "使用指南.md", (ROOT / "docs/public/首次使用指南.md").read_bytes())
    put_document(client, "LICENSE", (ROOT / "LICENSE").read_bytes())


def publish(client):
    folder = ROOT / "release-assets" / ("v" + __version__)
    review = json.loads((folder / "LOCAL-RELEASE-REVIEW.json").read_text(encoding="utf-8"))
    if review.get("public_release_ready") is not True:
        raise SystemExit("此候选版尚未完成公开发布前的材料或验证，未上传。")
    required = [f"LinguaFloat-v{__version__}-win-x64.zip", f"LinguaFloat-v{__version__}-corresponding-source.zip",
                "update.json", "update.json.sig", "SHA256SUMS.txt"]
    sys.path.insert(0, str(ROOT / "src"))
    from linguafloat.update_core import check_archive, load_channel, parse_manifest, sha256_file
    payload = (folder / "update.json").read_bytes()
    release = parse_manifest(payload, (folder / "update.json.sig").read_bytes(), load_channel(), __version__)
    check_archive(folder / required[0], release)
    checksums = dict(line.split("  ", 1)[::-1] for line in (folder / "SHA256SUMS.txt").read_text().splitlines())
    for name in required[:-1]:
        if sha256_file(folder / name) != checksums[name]:
            raise SystemExit("附件在审查后发生改变，未上传。")
    base = f"/repos/{OWNER}/linguafloat-releases/releases"
    # An initial public commit is needed before GitHub can create the release tag.
    documents(client)
    versions = request(client, "GET", base + "?per_page=100")
    matches = [r for r in versions if r["tag_name"] == f"v{__version__}"]
    if len(matches) > 1:
        raise SystemExit("发现重复草稿，请先核对，未覆盖。")
    if matches and not matches[0]["draft"]:
        raise SystemExit("此版本已公开，不覆盖历史附件。请使用新版本号。")
    notes = (ROOT / "docs/public/本版更新.md").read_text(encoding="utf-8")
    created = matches[0] if matches else request(client, "POST", base, json={"tag_name": f"v{__version__}", "name": f"LinguaFloat v{__version__}",
                      "body": notes + "\n\n请下载完整 win-x64.zip；对应源码另见 corresponding-source.zip。", "draft": True, "prerelease": False})
    url = created["upload_url"].split("{")[0]
    for name in required:
        path = folder / name
        digest = "sha256:" + sha256_file(path)
        uploaded = request(client, "GET", base + "/" + str(created["id"]) + "/assets?per_page=100")
        present = [a for a in uploaded if a["name"] == name]
        if present:
            asset = present[0]
            if asset.get("state") == "uploaded" and asset["size"] == path.stat().st_size and asset.get("digest") == digest:
                print("Verified existing draft asset: " + name, flush=True)
                continue
            raise SystemExit("草稿含不匹配或未完成附件，未覆盖：" + name)
        def chunks():
            with path.open("rb") as stream:
                yield from iter(lambda: stream.read(1024 * 1024), b"")
        print("Uploading: " + name + f" ({path.stat().st_size / 1024**2:.1f} MiB)", flush=True)
        response = client.post(url, params={"name": name}, content=chunks(),
                               headers={"Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)}, timeout=1800)
        if response.status_code != 201:
            raise SystemExit(f"附件上传失败：HTTP {response.status_code}。草稿仍未公开，可以检查后重试。")
        asset = response.json()
        if asset.get("size") != path.stat().st_size or asset.get("digest") != digest:
            raise SystemExit("GitHub 返回的附件大小或校验不匹配，草稿未公开。")
        print("Verified uploaded: " + name, flush=True)
    # Only a complete set of reviewed assets is published.
    uploaded = request(client, "GET", base + "/" + str(created["id"]) + "/assets?per_page=100")
    if {a["name"] for a in uploaded} != set(required):
        raise SystemExit("草稿附件集合不完整或含额外附件，未公开。")
    info = request(client, "PATCH", base + "/" + str(created["id"]), json={"draft": False})
    print(info["html_url"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "create-repositories", "documents", "publish"))
    args = parser.parse_args()
    with httpx.Client(timeout=30, headers={"Authorization": "Bearer " + credential(),
                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}) as client:
        user = request(client, "GET", "/user")
        if user["login"].casefold() != OWNER.casefold():
            raise SystemExit("当前登录账号与 astatineat2 不符，未创建或上传。")
        if args.command == "check":
            print("GitHub account verified: " + user["login"])
        elif args.command == "create-repositories":
            ensure_repositories(client)
        elif args.command == "documents":
            documents(client)
        else:
            publish(client)


if __name__ == "__main__":
    main()
