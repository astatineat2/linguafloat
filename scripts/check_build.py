"""Confirm the portable build contains the current Python code and assets."""
from pathlib import Path
import argparse
from types import CodeType
from PyInstaller.archive.readers import ZlibArchiveReader


def fingerprint(code):
    if not isinstance(code, CodeType):
        return code
    return (code.co_code, code.co_names, code.co_varnames, code.co_freevars,
            code.co_cellvars, code.co_flags, code.co_argcount, code.co_posonlyargcount,
            code.co_kwonlyargcount, code.co_exceptiontable,
            tuple(fingerprint(item) for item in code.co_consts))


root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--build-dir", type=Path, default=root / "build/LinguaFloat")
parser.add_argument("--dist-dir", type=Path, default=root / "exe")
args = parser.parse_args()
archive = ZlibArchiveReader(str(args.build_dir / "PYZ-00.pyz"))
checked = 0
for path in (root / "src/linguafloat").glob("*.py"):
    # Retired import-preview UI is retained as legacy source, not shipped.
    if path.stem in {"__main__", "cc_switch_ui"}:
        continue
    module = "linguafloat" if path.stem == "__init__" else f"linguafloat.{path.stem}"
    if path.stem in {"document_failures", "document_worker", "document_office", "document_pdf", "document_layout", "document_geometry", "document_scan_layout", "document_scan_recovery", "document_scientific", "document_artifacts", "document_frontmatter", "document_graphics", "document_graphic_layout", "document_references", "document_ocr"}:
        runtime_source = args.dist_dir / "document-runtime" / "src" / "linguafloat" / path.name
        assert runtime_source.read_bytes() == path.read_bytes(), f"Stale document worker: {path.name}"
        checked += 1
        continue
    expected = compile(path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True)
    actual = archive.extract(module)
    assert fingerprint(actual) == fingerprint(expected), f"Stale bundled module: {module}"
    checked += 1
for name in ("app.ico", "1.png", "backg.png", "chevron-down.png", "chevron-up.png", "check.png"):
    assert (args.dist_dir / "_internal/photo" / name).read_bytes() == (root / "photo" / name).read_bytes()
assert (args.dist_dir / "LinguaFloat.exe").is_file()
assert (args.dist_dir / "LinguaFloatUpdater.exe").is_file()
assert (args.dist_dir / "_internal/release/channel.json").read_bytes() == (root / "release/channel.json").read_bytes()
for name in ("__init__.py", "api_compat.py", "document_context.py", "document_core.py", "document_metrics.py", "document_scheduler.py", "document_artifacts.py", "document_office.py", "document_pdf.py", "document_layout.py", "document_geometry.py", "document_scan_layout.py", "document_scan_recovery.py", "document_scientific.py", "document_references.py", "document_worker.py"):
    assert (args.dist_dir / "document-runtime/src/linguafloat" / name).read_bytes() == (root / "src/linguafloat" / name).read_bytes()
assert (args.dist_dir / "document-runtime/worker_entry.py").read_bytes() == (root / "scripts/document_worker_entry.py").read_bytes()
print(f"Portable build verified: {checked} Python modules and 6 image assets match source.")
