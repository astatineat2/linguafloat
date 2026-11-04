"""Record the actual bundled OCR/font files and overlapping wheel provenance."""
import base64
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil

import yaml

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "app-dist/LinguaFloat"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assets = []
    rapid = BUNDLE / "_internal/rapidocr"
    catalog = yaml.safe_load((rapid / "default_models.yaml").read_text(encoding="utf-8"))
    def flatten(value):
        if isinstance(value, dict):
            if "model_dir" in value:
                yield value
            for child in value.values():
                yield from flatten(child)
    models = list(flatten(catalog))
    for path in sorted((rapid / "models").glob("*.onnx")):
        digest = sha(path)
        match = next((m for m in models if m.get("SHA256") == digest), None)
        if match is None:
            raise SystemExit("OCR model does not match the versioned upstream catalog: " + path.name)
        assets.append({"file": path.relative_to(BUNDLE).as_posix(), "sha256": digest,
                       "source": match["model_dir"], "license": "Apache-2.0",
                       "evidence": "LICENSES/assets/rapidocr-model-card.txt"})
    for path in sorted((BUNDLE / "_internal/qtawesome/fonts").glob("*.ttf")):
        name = path.name
        label = ("codicon" if name.startswith("codicon") else "elusive" if name.startswith("elusive") else
                 "fontawesome5" if name.startswith("fontawesome5") else "fontawesome6" if name.startswith("fontawesome6") else
                 "mdi5" if "icons5" in name else "mdi6" if "icons6" in name else
                 "phosphor" if name.startswith("phosphor") else "remixicon")
        assets.append({"file": path.relative_to(BUNDLE).as_posix(), "sha256": sha(path),
                       "evidence": "LICENSES/assets/" + label + ".txt"})
    (ROOT / "LICENSES/assets/bundled-files.json").write_text(json.dumps(assets, indent=2), encoding="utf-8")
    site = ROOT / ".document-runtime/Lib/site-packages"
    affected = ["cv2/typing/__init__.py", "cv2/version.py", "tencentcloud/__init__.py"]
    provenance = []
    for relative in affected:
        source = site / relative
        encoded = base64.urlsafe_b64encode(bytes.fromhex(sha(source))).decode().rstrip("=")
        owners = []
        for dist in metadata.distributions(path=[str(site)]):
            for item in dist.files or []:
                if str(item).replace("\\", "/") == relative:
                    owners.append({"distribution": dist.metadata["Name"], "version": dist.version,
                                   "record_matches": bool(item.hash and item.hash.value == encoded)})
        if not any(v["record_matches"] for v in owners):
            raise SystemExit("Unreconciled overlapping source: " + relative)
        target = ROOT / "source-materials/overrides" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        provenance.append({"path": relative, "sha256": sha(source), "wheel_records": owners,
                           "reason": "Overlapping installed distributions; actual source supplied verbatim."})
    (ROOT / "LICENSES/build-provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(f"Verified {len(assets)} bundled model/font files and {len(provenance)} shared source files.")


if __name__ == "__main__":
    main()
