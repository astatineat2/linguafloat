"""Replay an explicitly selected real translation snapshot, with networking forbidden."""
import ctypes
import json
import logging
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT / "src"))


def main():
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.CRITICAL)
    import pymupdf
    from linguafloat.document_core import DocumentJob, DocumentTranslator, JobControl, JobStore
    from linguafloat.document_artifacts import LayoutSnapshot
    from linguafloat.document_pdf import translate_layout_pdf
    source = Path("C:/Users/Astatine/xwechat_files/wxid_0hzsy59tmo9722_c17b/msg/file/2026-10/bose2022.pdf")
    snapshot = ROOT / "verification-bose-live/layout-cache/661b2299222dcc5eca3c075fabbadfe0beee50bc9f2735289cfc35b1c9904b2f.zip"
    output = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "verification-bose-live/repaired"
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(snapshot) as archive, pymupdf.open(source) as original, pymupdf.open(stream=archive.read("input.pdf"), filetype="pdf") as prepared:
        assert len(original) == len(prepared) == 8
        original_rects = [page.rect for page in original]
        for a, b in zip(original, prepared):
            assert a.rect == b.rect
            assert "".join(a.get_text().split()) == "".join(b.get_text().split())
        key = json.loads(archive.read("manifest.json"))["key"]
    init = LayoutSnapshot.__init__
    def selected(self, *args, **kwargs):
        init(self, *args, **kwargs)
        # Diagnostic-only explicit snapshot selection; production matching is unchanged.
        self.key, self.path = key, snapshot
    LayoutSnapshot.__init__ = selected
    profile = dict(id="explicit-real-snapshot", model="gpt-6.1-sol", api_mode="responses", base_url="https://example.invalid")
    job = DocumentJob(str(source), str(output), profile, pdf_mode="layout", relayout_only=True)
    def forbidden(**kwargs):
        raise AssertionError("API forbidden in snapshot replay")
    events = []
    store = JobStore(output / "replay.sqlite3")
    try:
        translator = DocumentTranslator(job, "", store, JobControl(), events.append, NS(responses=NS(create=forbidden)))
        paths, warnings = translate_layout_pdf(job, translator, output, events.append)
        report = dict(outputs=paths, warnings=warnings, api_requests=translator.calls,
            source_verified=True, response_mode="real user translation snapshot; no new model responses",
            events=events)
        (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Real cached translation replay saved; API requests:", translator.calls)
        for path in paths:
            with pymupdf.open(path) as pdf:
                assert len(pdf) == 8
                if "译文" in path:
                    assert [p.rect for p in pdf] == original_rects
        with pymupdf.open(paths[0]) as pdf:
            pdf[1].get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(35, 302, 558, 480)).save(output / "page2-detail.png")
    finally:
        store.close()
        LayoutSnapshot.__init__ = init


if __name__ == "__main__":
    main()
