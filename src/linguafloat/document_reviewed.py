"""Opt-in, local reviewed pages. Never bind corrections by filename alone."""
import hashlib
import json
import re
from pathlib import Path


def local_review(store, digest):
    path=getattr(store,'path',None)
    if not path or not re.fullmatch('[0-9a-f]{64}',digest): return {},None
    home=Path(path).parent/'reviewed-documents-v1'/digest
    try:
        manifest=json.loads((home/'review.json').read_text('utf8'))
        if manifest.get('schema')==1 and manifest.get('source_sha256')==digest: return manifest,home
    except (OSError,ValueError,AttributeError):pass
    return {},None


def reviewed_pages(store,digest,job):
    manifest,home=local_review(store,digest)
    key='/'.join(str(getattr(job,name,'')) for name in ('target','chinese_font','latin_font'))
    result={}
    for number,checksum in manifest.get('page_sets',{}).get(key,{}).items():
        if not number.isdecimal() or not re.fullmatch('[0-9a-f]{64}',checksum):continue
        file=home/(checksum+'.pdf')
        try:
            with file.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()==checksum:result[int(number)-1]=file
        except OSError:continue
    return result
