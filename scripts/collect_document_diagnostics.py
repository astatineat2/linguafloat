"""Copy current-user document caches for isolated, credential-free diagnosis."""
import json
import os
import shutil
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = root / "verification-bose-live"
output.mkdir(exist_ok=True)
data = Path(os.environ["APPDATA"]) / "LinguaFloat"
try:
    config = json.loads((data / "config.json").read_text(encoding="utf-8"))
    safe = {"active_provider": config.get("active_provider"), "providers": [
        {k: p.get(k) for k in ("id", "name", "base_url", "model", "api_mode", "document_concurrency", "document_rpm", "document_tpm", "retries")}
        for p in config.get("providers", [])]}
    (output / "profiles.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    with sqlite3.connect((data / "documents/jobs.sqlite3").as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(output / "jobs.sqlite3") as target:
        source.backup(target)
        rows = source.execute("SELECT id,source,status,details FROM jobs ORDER BY updated DESC LIMIT 15").fetchall()
    shutil.copytree(data / "documents/layout-cache", output / "layout-cache", dirs_exist_ok=True)
    (output / "jobs.json").write_text(json.dumps([dict(id=i, source=s, status=t, details=json.loads(d)) for i,s,t,d in rows], ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "collection.json").write_text(json.dumps(dict(data_directory=str(data), status="copied")), encoding="utf-8")
except Exception as error:
    (output / "collection.json").write_text(json.dumps(dict(error=type(error).__name__)), encoding="utf-8")
    raise
