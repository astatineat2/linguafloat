"""Prepare exact copyleft sources and notices; never install or execute downloads."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "source-materials"


def read_url(url, limit=4 * 1024 * 1024):
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Source metadata exceeds limit")
                return data
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(1 + attempt)
    raise last


def download(url, filename, expected):
    path = DEST / filename
    if path.is_file() and (expected is None or digest(path) == expected):
        return path
    temporary = path.with_suffix(path.suffix + ".part")
    last = None
    for attempt in range(8):
        try:
            offset = temporary.stat().st_size if temporary.exists() else 0
            request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
            with urllib.request.urlopen(request, timeout=90) as incoming:
                resumed = offset and incoming.status == 206 and incoming.headers.get("Content-Range", "").startswith(f"bytes {offset}-")
                mode = "ab" if resumed else "wb"
                with temporary.open(mode) as output:
                    while chunk := incoming.read(1024 * 1024):
                        output.write(chunk)
            if expected is not None and digest(temporary) != expected:
                raise ValueError("Source checksum mismatch: " + filename)
            temporary.replace(path)
            return path
        except Exception as exc:
            last = exc
            if attempt < 7:
                time.sleep(1 + attempt)
    raise last


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pypi_source(name, version):
    info = json.loads(read_url(f"https://pypi.org/pypi/{name}/{version}/json"))
    candidates = [v for v in info["urls"] if v["packagetype"] == "sdist"]
    if not candidates:
        raise ValueError("No source distribution for " + name)
    value = candidates[0]
    return {"component": name, "version": version, "file": value["filename"], "url": value["url"],
            "sha256": value["digests"]["sha256"], "size": value["size"]}


def qt_source(name):
    filename = name + "-everywhere-src-6.11.2.tar.xz"
    base = ("https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/"
            if name == "pyside-setup" else "https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/")
    url = base + filename
    expected = read_url(url + ".sha256", limit=4096).decode("ascii").split()[0]
    return {"component": name, "version": "6.11.2", "file": filename, "url": url, "sha256": expected}


def collect_source_notices(archive, label):
    destination = ROOT / "LICENSES/upstream" / label
    saved = []
    def accept(name, size):
        base = Path(name).name.lower()
        return size < 4 * 1024 * 1024 and (base.startswith(("license", "copying", "notice")) or base == "copyright")
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                if accept(item.filename, item.file_size) and not item.is_dir():
                    relative = Path(*Path(item.filename).parts[1:])
                    if ".." in relative.parts:
                        raise ValueError("Unexpected source archive path")
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read(item))
                    saved.append(target.relative_to(ROOT).as_posix())
    else:
        with tarfile.open(archive) as source:
            for item in source:
                if item.isfile() and accept(item.name, item.size):
                    relative = Path(*Path(item.name).parts[1:])
                    if ".." in relative.parts:
                        raise ValueError("Unexpected source archive path")
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.extractfile(item).read())
                    saved.append(target.relative_to(ROOT).as_posix())
    return saved


def process(value):
    upstream_digest = value.get("sha256")
    path = download(value["url"], value["file"], value["sha256"])
    value["size"] = path.stat().st_size
    value["sha256"] = digest(path)
    value["notices"] = collect_source_notices(path, value["component"])
    value["checksum_verified"] = bool(upstream_digest)
    value["checksum_evidence"] = "upstream published SHA-256" if upstream_digest else "locally calculated SHA-256 of HTTPS official tagged source"
    print(f"Verified {value['component']} {value['version']}: {value['size']} bytes, {len(value['notices'])} notices", flush=True)
    return value


def main():
    lock = json.loads((DEST / "source-lock.json").read_text(encoding="utf-8"))
    wanted = [("BabelDOC", "0.6.2"), ("pdf2zh-next", "2.9.0"), ("PyMuPDF", "1.25.2"),
              ("pynput", "1.8.2"), ("Levenshtein", "0.27.5"), ("marisa-trie", "1.4.1"),
              ("orjson", "3.13.0"), ("certifi", "2026.7.22"), ("tqdm", "4.70.1"),
              ("antlr4-python3-runtime", "4.9.3"), ("ffmpy", "1.0.0"),
              ("gradio_client", "1.10.4"), ("tencentcloud-sdk-python-common", "3.1.186"),
              ("tencentcloud-sdk-python-tmt", "3.1.129"), ("qtawesome", "1.4.2")]
    sources = []
    sources.extend([
        {"component": "flatbuffers", "version": "25.12.19", "file": "flatbuffers-25.12.19.tar.gz",
         "url": "https://codeload.github.com/google/flatbuffers/tar.gz/refs/tags/v25.12.19", "sha256": None},
        {"component": "MuPDF", "version": "1.25.2", "file": "mupdf-1.25.2-source.tar.gz",
         "url": "https://mupdf.com/downloads/archive/mupdf-1.25.2-source.tar.gz", "sha256": None},
        {"component": "RapidOCR", "version": "3.9.2", "file": "RapidOCR-3.9.2.tar.gz",
         "url": "https://codeload.github.com/RapidAI/RapidOCR/tar.gz/refs/tags/v3.9.2", "sha256": None}
    ])
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(pypi_source, *item) for item in wanted]
        tasks += [pool.submit(qt_source, n) for n in ("pyside-setup", "qtbase", "qtdeclarative", "qtsvg", "qtdatavis3d", "qtvirtualkeyboard", "qtwebengine")]
        for future in as_completed(tasks):
            sources.append(future.result())
    lock["archives"] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(process, value) for value in sources]):
            lock["archives"].append(future.result())
            (DEST / "source-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
