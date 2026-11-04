"""Read-only source inspection / local OCR. Never performs a translation request."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import pymupdf as f
from linguafloat.document_graphics import GraphicAnalyzer, source_digest
from linguafloat.document_core import JobStore, JobControl

OUT = ROOT / 'artifacts/hobson-v0.9.2'
SOURCE = next(Path('C:/Users/Astatine/Zotero/storage/3U8UUC6T').glob('*.pdf'))
if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    store = JobStore(OUT / 'local-ocr.sqlite3')
    analyzer = GraphicAnalyzer(store, JobControl(), lambda e: print(e.get('message',''), flush=True))
    digest = source_digest(SOURCE)
    result = {}
    with f.open(SOURCE) as doc:
        for number in range(len(doc)):
            areas = analyzer.discover(doc[number], digest)
            result[str(number + 1)] = areas
            for a in areas:
                print(a['id'], a['kind'], [(r['text'], round(r['confidence'],3)) for r in a['regions']], flush=True)
    (OUT / 'discovery.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    store.close()
