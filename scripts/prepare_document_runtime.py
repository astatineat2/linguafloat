"""Assemble a relocatable Windows Python runtime with pinned document dependencies."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    base = Path(sys.base_prefix)
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("python.exe", "pythonw.exe", "python313.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        source = base / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    shutil.copytree(base / "DLLs", destination / "DLLs", dirs_exist_ok=True)
    shutil.copytree(base / "Lib", destination / "Lib", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip"))
    packages = Path(sys.prefix) / "Lib" / "site-packages"
    shutil.copytree(packages, destination / "Lib" / "site-packages", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "pip", "pip-*.dist-info", "pytest", "_pytest", "pytest-*.dist-info"))
    # Explicit paths make the runtime independent of Python installs and user site packages.
    (destination / "python313._pth").write_text(".\nDLLs\nLib\nLib/site-packages\nimport site\n", encoding="ascii")
    target_source = destination / "src" / "linguafloat"
    target_source.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "api_compat.py", "document_core.py", "document_metrics.py", "document_scheduler.py", "document_artifacts.py", "document_office.py", "document_pdf.py", "document_layout.py", "document_geometry.py", "document_scan_layout.py", "document_scan_recovery.py", "document_scientific.py", "document_worker.py"):
        shutil.copy2(project / "src" / "linguafloat" / name, target_source / name)
    for name in ("document_frontmatter.py","document_graphics.py", "document_graphic_layout.py", "document_references.py"):
        shutil.copy2(project / "src" / "linguafloat" / name,target_source / name)
    shutil.copy2(project / "scripts" / "document_worker_entry.py", destination / "worker_entry.py")
    installed = sorted({f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
                        if d.metadata['Name'].lower() != 'linguafloat'})
    (destination / "DEPENDENCIES.txt").write_text("\n".join(installed) + "\n", encoding="utf-8")
    (project / "document-requirements.lock.txt").write_text("\n".join(installed) + "\n", encoding="utf-8")
    print(f"Portable document runtime: {destination}")


if __name__ == "__main__":
    main()
