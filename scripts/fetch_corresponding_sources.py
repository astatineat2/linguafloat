"""Fetch exact upstream source archives for review, never install dependencies."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / "source-materials"
    lock = json.loads((destination / "source-lock.json").read_text(encoding="utf-8"))
    fetched = []
    for name, version in (("babeldoc", "0.6.2"), ("pdf2zh-next", "2.9.0"), ("PyMuPDF", "1.25.2")):
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
            info = json.load(response)
        source = next(item for item in info["urls"] if item["packagetype"] == "sdist")
        path = destination / source["filename"]
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(source["url"], timeout=45) as incoming, temporary.open("wb") as output:
                while chunk := incoming.read(1024 * 1024):
                    output.write(chunk)
            temporary.rename(path)
        digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
        if digest != source["digests"]["sha256"]:
            raise RuntimeError("Source archive checksum mismatch: " + name)
        fetched.append({"component": name, "version": version, "file": path.name, "sha256": digest,
                        "url": source["url"], "size": path.stat().st_size, "reviewed": False})
        print(f"Fetched and verified {name} {version}: {path.stat().st_size} bytes", flush=True)
    lock["archives"] = fetched
    (destination / "source-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
