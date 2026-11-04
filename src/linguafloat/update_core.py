"""Signed release metadata and bounded downloads. No translation credentials are used."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import struct
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from packaging.version import InvalidVersion, Version


class UpdateError(RuntimeError):
    pass


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def update_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "LinguaFloat" / "updates"


def load_channel() -> dict:
    import sys
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    path = root / "release" / "channel.json"
    try:
        channel = json.loads(path.read_text(encoding="utf-8"))
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", channel["repository"]):
            raise ValueError()
        return channel
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UpdateError("更新来源尚未配置，当前程序可以继续使用。") from exc


def verify_signature(payload: bytes, signature: bytes, public_key: dict) -> None:
    """Use Windows CNG RSA/SHA-256 (PKCS#1 v1.5), without shipping a crypto runtime."""
    if os.name != "nt":
        raise UpdateError("自动安装更新目前仅支持 Windows。")
    try:
        modulus = int(public_key["n"], 16)
        exponent = int(public_key["e"])
        if not 3072 <= modulus.bit_length() <= 8192 or exponent != 65537:
            raise ValueError()
        signature = base64.b64decode(signature.strip(), validate=True)
        n = modulus.to_bytes((modulus.bit_length() + 7) // 8, "big")
        e = exponent.to_bytes(3, "big")
        if len(signature) != len(n):
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError("更新签名或受信公钥格式无效。") from exc
    api = ctypes.WinDLL("bcrypt")
    handle = ctypes.c_void_p()
    key = ctypes.c_void_p()
    api.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    api.BCryptImportKeyPair.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    api.BCryptVerifySignature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    api.BCryptDestroyKey.argtypes = [ctypes.c_void_p]
    api.BCryptCloseAlgorithmProvider.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

    class Padding(ctypes.Structure):
        _fields_ = [("algorithm", ctypes.c_wchar_p)]

    blob = struct.pack("<6I", 0x31415352, len(n) * 8, len(e), len(n), 0, 0) + e + n
    digest = hashlib.sha256(payload).digest()
    try:
        if api.BCryptOpenAlgorithmProvider(ctypes.byref(handle), "RSA", None, 0) != 0:
            raise UpdateError("Windows 无法初始化更新签名校验。")
        if api.BCryptImportKeyPair(handle, None, "RSAPUBLICBLOB", ctypes.byref(key), blob, len(blob), 0) != 0:
            raise UpdateError("无法读取更新公钥。")
        padding = Padding("SHA256")
        if api.BCryptVerifySignature(key, ctypes.byref(padding), digest, len(digest), signature, len(signature), 2) != 0:
            raise UpdateError("更新签名校验失败，未安装任何内容。")
    finally:
        if key.value:
            api.BCryptDestroyKey(key)
        if handle.value:
            api.BCryptCloseAlgorithmProvider(handle, 0)


def github_url(url: str, repository: str, *, asset: bool = False) -> str:
    parsed = urlparse(url)
    prefix = "/" + repository + "/releases/"
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or not parsed.path.startswith(prefix)
            or (asset and not parsed.path.startswith(prefix + "download/")) or parsed.query or parsed.fragment):
        raise UpdateError("更新地址不属于已配置的 GitHub 发布仓库。")
    return url


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    unpacked_size: int
    sha256: str
    notes: str
    page: str
    tag: str


def parse_manifest(payload: bytes, signature: bytes, channel: dict, current: str) -> Release:
    verify_signature(payload, signature, channel["public_key"])
    try:
        value = json.loads(payload)
        version = Version(value["version"])
        minimum = Version(value["minimum_updater"])
        if (value["schema"] != 1 or value["architecture"] != "win-x64" or value["repository"] != channel["repository"]
                or value.get("candidate_only") is True):
            raise ValueError()
        if version.is_prerelease or version.is_devrelease or version.local is not None:
            raise ValueError()
        if minimum > Version(current):
            raise UpdateError("此版本需要先手动安装新版更新助手，请从发布页下载完整包。")
        size, unpacked = value["size"], value["unpacked_size"]
        if type(size) is not int or type(unpacked) is not int or not 0 < size < 2**31 or not 0 < unpacked < 20 * 2**30:
            raise ValueError()
        if not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
            raise ValueError()
        tag = "v" + str(version)
        url = github_url(value["url"], channel["repository"], asset=True)
        page = github_url(value["page"], channel["repository"])
        if f"/download/{tag}/" not in url or page != f"https://github.com/{channel['repository']}/releases/tag/{tag}":
            raise ValueError()
        return Release(str(version), url, size, unpacked, value["sha256"], str(value.get("notes", ""))[:20000], page, tag)
    except (KeyError, TypeError, ValueError, InvalidVersion) as exc:
        raise UpdateError("更新说明的版本、架构或文件信息无效。") from exc


def _safe_response(response):
    """Follow only GitHub's HTTPS asset infrastructure, including its signed CDN URLs."""
    for item in [*response.history, response]:
        parsed = urlparse(str(item.url))
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host in {"github.com", "api.github.com"} or host.endswith(".githubusercontent.com")):
            raise UpdateError("更新下载被重定向到不受信任的地址。")


def _response_error(response):
    if response.status_code in (403, 429):
        raise UpdateError("GitHub 暂时限制访问频率，请稍后再试；翻译功能不受影响。")
    if response.status_code == 404:
        raise UpdateError("尚无可下载的公开正式版本，或发布仓库尚未创建。")
    if response.status_code != 200:
        raise UpdateError(f"更新服务返回 HTTP {response.status_code}，请稍后重试。")


def fetch_bytes(client, url: str, *, limit: int = 512 * 1024) -> bytes:
    with client.stream("GET", url) as response:
        _safe_response(response)
        _response_error(response)
        parts, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise UpdateError("更新信息超过允许大小。")
            parts.append(chunk)
        return b"".join(parts)


def _check_signed_latest(client, channel: dict, current: str) -> Release | None:
    # Public release assets have their own delivery endpoint. Respect the API
    # backoff while still checking publisher-authenticated stable metadata.
    base = f"https://github.com/{channel['repository']}/releases/latest/download/"
    payload = fetch_bytes(client, base + "update.json")
    signature = fetch_bytes(client, base + "update.json.sig", limit=4096)
    release = parse_manifest(payload, signature, channel, current)
    return release if Version(release.version) > Version(current) else None


def check_latest(client, channel: dict, current: str, cache: Path, *, now: float | None = None) -> Release | None:
    now = time.time() if now is None else now
    try:
        state = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if now < state.get("retry_after", 0):
        return _check_signed_latest(client, channel, current)
    endpoint = f"https://api.github.com/repos/{channel['repository']}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}
    if state.get("etag") and state.get("repository") == channel["repository"]:
        headers["If-None-Match"] = state["etag"]
    response = client.get(endpoint, headers=headers)
    _safe_response(response)
    if response.status_code in (403, 429):
        try:
            until = float(response.headers.get("X-RateLimit-Reset", 0))
            delay = float(response.headers.get("Retry-After", 60))
        except ValueError:
            until, delay = 0, 60
        state["retry_after"] = min(now + 3600, max(now + max(delay, 60), until))
        atomic_json(cache, state)
        return _check_signed_latest(client, channel, current)
    if response.status_code == 304 and state.get("payload") and state.get("signature"):
        release = parse_manifest(base64.b64decode(state["payload"]), state["signature"].encode(), channel, current)
        return release if Version(release.version) > Version(current) else None
    _response_error(response)
    if len(response.content) > 2 * 1024 * 1024:
        raise UpdateError("GitHub 发布信息超过允许大小。")
    try:
        info = response.json()
        if info.get("draft") or info.get("prerelease"):
            raise UpdateError("未找到适用的正式版本。")
        tag = info["tag_name"]
        version = Version(tag.removeprefix("v"))
        if version <= Version(current) and not version.is_prerelease:
            return None
        assets = {item["name"]: item["browser_download_url"] for item in info["assets"]}
        metadata_url = github_url(assets["update.json"], channel["repository"], asset=True)
        signature_url = github_url(assets["update.json.sig"], channel["repository"], asset=True)
        payload, signature = fetch_bytes(client, metadata_url), fetch_bytes(client, signature_url, limit=4096)
        release = parse_manifest(payload, signature, channel, current)
        if release.tag != tag:
            raise UpdateError("发布标签与签名版本不一致。")
        atomic_json(cache, {"repository": channel["repository"], "etag": response.headers.get("ETag", ""),
                            "payload": base64.b64encode(payload).decode(), "signature": signature.decode("ascii")})
        return release if Version(release.version) > Version(current) else None
    except (KeyError, TypeError, ValueError, InvalidVersion) as exc:
        raise UpdateError("发布附件不完整，缺少签名更新信息；请手动查看发布页。") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_archive(path: Path, release: Release) -> None:
    if path.stat().st_size != release.size or sha256_file(path) != release.sha256:
        raise UpdateError("更新包大小或 SHA-256 校验失败，请重新下载；当前版本未更改。")
    try:
        with zipfile.ZipFile(path) as archive:
            names, total = set(), 0
            for item in archive.infolist():
                part = PurePosixPath(item.filename)
                # Also reject Windows aliases, ADS, device names and case collisions.
                if (not part.parts or part.is_absolute() or "\\" in item.filename
                        or any(p in {".", ".."} or p.endswith((".", " ")) or ":" in p
                               or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in part.parts)
                        or any(p in {"", ".", ".."} for p in item.filename.rstrip("/").split("/"))
                        or (item.external_attr >> 16) & 0o170000 == 0o120000 or item.flag_bits & 1):
                    raise UpdateError("更新包包含不安全的文件路径。")
                normalized = item.filename.rstrip("/").casefold()
                if normalized in names:
                    raise UpdateError("更新包包含重复文件路径。")
                names.add(normalized)
                total += item.file_size
                if len(names) > 100000 or total > release.unpacked_size:
                    raise UpdateError("更新包解压大小或文件数量与声明不符。")
            required = {"linguafloat.exe", "linguafloatupdater.exe", "version.txt", "document-runtime/python.exe",
                        "document-runtime/worker_entry.py", "_internal/release/channel.json"}
            if not required <= names or not any(n.startswith("_internal/") for n in names) or total != release.unpacked_size:
                raise UpdateError("更新包不是完整便携版，或解压大小不符。")
            version_text = archive.read("VERSION.txt").decode("utf-8")
            if version_text.splitlines()[0] != f"LinguaFloat v{release.version}":
                raise UpdateError("更新包内的软件版本与签名声明不一致。")
    except (zipfile.BadZipFile, OSError, KeyError, UnicodeError) as exc:
        raise UpdateError("更新压缩包损坏或无法读取。") from exc


def download_release(client, release: Release, directory: Path, progress, cancelled) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "package.zip"
    if target.exists():
        check_archive(target, release)
        return target
    if shutil.disk_usage(directory).free < release.size + release.unpacked_size + 64 * 1024**2:
        raise UpdateError("下载目录磁盘空间不足，需要为下载和解压预留空间。")
    partial = directory / "package.part"
    offset = partial.stat().st_size if partial.exists() else 0
    if offset >= release.size:
        partial.unlink()
        offset = 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with client.stream("GET", release.url, headers=headers) as response:
        _safe_response(response)
        if response.status_code == 206 and offset:
            if response.headers.get("Content-Range", "").split("/")[0] != f"bytes {offset}-{release.size - 1}":
                raise UpdateError("服务器返回的续传范围不正确，请重新下载。")
        elif response.status_code == 200:
            offset = 0
        else:
            _response_error(response)
        with partial.open("ab" if offset else "wb") as output:
            size = offset
            for chunk in response.iter_bytes(256 * 1024):
                if cancelled.is_set():
                    raise UpdateError("下载已暂停，再次下载时将尝试续传。")
                size += len(chunk)
                if size > release.size:
                    raise UpdateError("更新包超过声明大小，下载已停止。")
                output.write(chunk)
                progress(size, release.size)
    try:
        check_archive(partial, release)
    except UpdateError:
        # Keep interrupted downloads, but do not repeatedly resume confirmed corrupt data.
        if partial.stat().st_size == release.size:
            partial.rename(directory / f"rejected-{time.time_ns()}.zip")
        raise
    partial.rename(target)
    return target
