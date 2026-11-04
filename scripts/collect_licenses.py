"""Collect notices and exact installed versions from both build environments."""
from __future__ import annotations
import argparse
import importlib.metadata as metadata
import json
import shutil
from pathlib import Path


def collect(site: Path, destination: Path):
    inventory = []
    for dist in sorted(metadata.distributions(path=[str(site)]), key=lambda d: d.metadata.get("Name", "").lower()):
        name = dist.metadata.get("Name", "unknown")
        if name.lower() == "linguafloat":
            continue  # The application is built from this checkout, not its old editable metadata.
        license_files = []
        for item in dist.files or []:
            filename = str(item).replace("\\", "/")
            basename = item.name.lower()
            if ((".dist-info/" in filename and "/licenses/" in filename)
                    or basename in {"license", "license.txt", "license.md", "copying", "copying.txt", "notice", "notice.txt", "thirdpartynotices.txt"}):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    relative = Path(*item.parts[1:])
                    target = destination / name / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    license_files.append(str(target.relative_to(destination)))
        inventory.append({"name": name, "version": dist.version, "license_expression": dist.metadata.get("License-Expression", ""),
                          "declared_license": dist.metadata.get("License", "")[:4000], "license_files": license_files,
                          "project_urls": dist.metadata.get_all("Project-URL") or []})
    return inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    inventory = collect(args.site, args.output)
    (args.output / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Collected {len(inventory)} dependency records.")


if __name__ == "__main__":
    main()
