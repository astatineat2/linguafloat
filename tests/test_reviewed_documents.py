import hashlib
import json
from types import SimpleNamespace as NS
from linguafloat.document_reviewed import reviewed_pages


def test_reviewed_page_is_local_hash_and_font_bound(tmp_path):
    digest='1'*64;home=tmp_path/'reviewed-documents-v1'/digest;home.mkdir(parents=True)
    data=b'checked local fixture';checksum=hashlib.sha256(data).hexdigest();f=home/(checksum+'.pdf');f.write_bytes(data)
    (home/'review.json').write_text(json.dumps(dict(schema=1,source_sha256=digest,page_sets={'zh-Hans/simsun/times':{'5':checksum}})))
    store=NS(path=tmp_path/'jobs.sqlite3'); job=NS(target='zh-Hans',chinese_font='simsun',latin_font='times')
    assert reviewed_pages(store,digest,job)=={4:f}
    assert not reviewed_pages(store,'2'*64,job)
    job.chinese_font='msyh';assert not reviewed_pages(store,digest,job)
    job.chinese_font='simsun';f.write_bytes(b'bad');assert not reviewed_pages(store,digest,job)
