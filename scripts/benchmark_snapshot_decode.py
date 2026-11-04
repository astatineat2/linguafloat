"""Compare the existing data-only snapshot against the optimized schema decoder."""
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat.document_artifacts import LayoutSnapshot
from babeldoc.format.pdf.document_il.xml_converter import XMLConverter

folder = ROOT / "verification-pdf-efficiency"
old_path = folder / "before/src/linguafloat/document_artifacts.py"
spec = importlib.util.spec_from_file_location("previous_snapshot", old_path)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
source = next((ROOT / "verification-speed/tian-portable/layout-cache").glob("*.zip"))
work = folder / "snapshot-comparison"
work.mkdir(parents=True, exist_ok=True)
config = SimpleNamespace(get_working_file_path=lambda name: work / name)
records = []
reference = None
for name, kind in (("before", previous.LayoutSnapshot), ("after", LayoutSnapshot)):
    snapshot = object.__new__(kind)
    snapshot.path, snapshot.key = source, source.stem
    start = time.perf_counter()
    result = snapshot.load(config)
    elapsed = time.perf_counter() - start
    assert result is not None
    docs, prepared, boxes = result
    content = XMLConverter().to_json(docs)
    if reference is None:
        reference = (content, prepared.read_bytes(), boxes)
    else:
        if reference[0] != content:
            def differences(a, b, path="root"):
                if isinstance(a, dict) and isinstance(b, dict):
                    for key in a.keys() | b.keys():
                        yield from differences(a.get(key), b.get(key), path + "." + key)
                elif isinstance(a, list) and isinstance(b, list):
                    if len(a) != len(b):
                        yield (path, len(a), len(b))
                    for index, (left, right) in enumerate(zip(a, b)):
                        yield from differences(left, right, path + f"[{index}]")
                elif type(a) is not type(b) or a != b:
                    yield (path, str(a)[:80], str(b)[:80])
            import itertools
            print(list(itertools.islice(differences(json.loads(reference[0]), json.loads(content)), 10)))
        assert reference == (content, prepared.read_bytes(), boxes), "Snapshot semantics changed"
    records.append(dict(version=name, seconds=elapsed, pages=len(docs.page), identical=True))
(work / "results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
print(json.dumps(records, indent=2))
