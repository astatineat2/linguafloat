"""Versioned, data-only translated PDF snapshots for local re-layout."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import fields, is_dataclass
from functools import cache
from types import UnionType
from typing import get_args, get_origin
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


@cache
def _decoder(kind):
    """Compile the pinned dataclass schema once; no types are taken from cache data."""
    if get_origin(kind) is UnionType:
        choices = [value for value in get_args(kind) if value is not type(None)]
        if len(choices) != 1:
            raise TypeError("Unsupported snapshot union")
        decode = _decoder(choices[0])
        return lambda value: None if value is None else decode(value)
    if get_origin(kind) is list:
        decode = _decoder(get_args(kind)[0])
        def sequence(value):
            if not isinstance(value, list):
                raise TypeError("Expected snapshot list")
            return [decode(item) for item in value]
        return sequence
    if is_dataclass(kind):
        members = {item.name: _decoder(item.type) for item in fields(kind)}
        def construct(value):
            if not isinstance(value, dict):
                raise TypeError("Expected snapshot object")
            return kind(**{name: members[name](item) for name, item in value.items()})
        return construct
    if kind is object:
        return lambda value: value
    if kind not in (str, int, float, bool):
        raise TypeError("Unsupported snapshot primitive")
    def primitive(value):
        if type(value) is kind:
            return value
        # Match xsdata's existing conversion behavior, including legacy debug fields.
        from xsdata.formats.converter import converter
        from xsdata.exceptions import ConverterError
        value = converter.serialize(value)
        try:
            return converter.deserialize(value, [kind])
        except ConverterError:
            return value
    return primitive


class LayoutSnapshot:
    def __init__(self, directory, job, pages, policy_key=""):
        from importlib.metadata import version
        with open(job.source, "rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        scope = dict(schema=1, source=digest, pages=pages, target=job.target, language=job.source_language,
                     provider={k: job.provider.get(k) for k in ("id", "base_url", "model", "api_mode")},
                     versions={name: version(name) for name in ("babeldoc", "pdf2zh-next", "pymupdf", "xsdata")},
                     translation_policy="documents-v1-subscript-v1-no-glossary-no-table")
        from linguafloat.api_compat import request_options
        options = request_options(job.provider)
        if options:
            scope["request_options"] = options
        if policy_key:
            scope["scientific_policy"] = policy_key
        self.key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        self.folder = Path(directory) / "layout-cache"
        self.path = self.folder / (self.key + ".zip")

    def load(self, config, emit=None):
        if not self.path.is_file():
            return None
        try:
            from babeldoc.format.pdf.document_il import il_version_1
            def measured(name, started):
                if emit:
                    emit({"type": "measurement", "name": name, "seconds": time.perf_counter() - started})
            started = time.perf_counter()
            with ZipFile(self.path) as archive:
                if sum(item.file_size for item in archive.infolist()) > 300 * 1024 ** 2:
                    return None
                manifest = json.loads(archive.read("manifest.json"))
                if manifest["key"] != self.key:
                    return None
                data = {name: archive.read(name) for name in ("translated.json", "input.pdf")}
                measured("排版缓存读取及解压", started)
                started = time.perf_counter()
                if any(hashlib.sha256(value).hexdigest() != manifest["hashes"][name] for name, value in data.items()):
                    return None
                measured("排版缓存完整性校验", started)
            started = time.perf_counter()
            docs = _decoder(il_version_1.Document)(json.loads(data["translated.json"]))
            measured("排版缓存对象重建", started)
            if not docs.page:
                return None
            target = config.get_working_file_path("snapshot-input.pdf")
            target.write_bytes(data["input.pdf"])
            boxes = {int(k): v for k, v in manifest["mediabox"].items()}
            os.utime(self.path, None)
            return docs, target, boxes
        except (OSError, ValueError, KeyError, TypeError, BadZipFile):
            return None
        except Exception as exc:
            # Schema decoding errors invalidate only this optional cache.
            if type(exc).__module__.startswith("xsdata"):
                return None
            raise

    def save(self, docs, config, mediabox):
        from babeldoc.format.pdf.document_il.xml_converter import XMLConverter
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"translated.json": XMLConverter().to_json(docs).encode(),
                "input.pdf": config.get_working_file_path("input.pdf").read_bytes()}
        if sum(map(len, data.values())) > 300 * 1024 ** 2:
            return False
        manifest = dict(key=self.key, mediabox=mediabox,
                        hashes={name: hashlib.sha256(value).hexdigest() for name, value in data.items()})
        temporary = self.folder / (self.key + "." + uuid4().hex + ".tmp")
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                for name, value in data.items():
                    archive.writestr(name, value)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        # Snapshots are disposable; cap the cache independently of translation text.
        files = sorted(self.folder.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for path in files:
            total += path.stat().st_size
            if total > 512 * 1024 ** 2 and path != self.path:
                path.unlink(missing_ok=True)
        return True
