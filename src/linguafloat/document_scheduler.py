"""Bounded document requests with a persistent rolling quota per provider/model."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from email.utils import parsedate_to_datetime


def retry_delay(exc, attempt):
    headers = getattr(getattr(exc, "response", None), "headers", {})
    value = headers.get("retry-after") if headers else None
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                seconds = 0
        if seconds > 0:
            return seconds
    import random
    return min(30, 2 ** (attempt + 1)) + random.uniform(0, .5)


class RequestGate:
    def __init__(self, provider, store, control, wait):
        self.store, self.control, self.wait = store, control, wait
        def number(key, default, maximum):
            value = provider.get(key, default)
            return max(0, min(maximum, value)) if type(value) is int else default
        self.maximum = max(1, number("document_concurrency", 2, 6))
        self.limit = self.maximum
        self.rpm = max(1, number("document_rpm", 60, 10000))
        self.tpm = number("document_tpm", 0, 100000000)
        self.scope = hashlib.sha256(json.dumps([provider.get(k, "") for k in
            ("id", "base_url", "model")], ensure_ascii=False).encode()).hexdigest()
        self.lock = threading.RLock()
        self.inflight = self.successes = 0
        self.cooldown = 0.0
        with store.lock, store.db:
            store.db.execute("CREATE TABLE IF NOT EXISTS request_quota (id TEXT PRIMARY KEY, scope TEXT, started REAL, tokens INTEGER)")
            store.db.execute("CREATE INDEX IF NOT EXISTS request_quota_scope ON request_quota(scope, started)")

    def acquire(self, messages):
        # Conservative byte-based input/output reservation; actual usage reconciles it.
        size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
        tokens = size + max(size, 4096)
        if self.tpm and tokens > self.tpm:
            from linguafloat.document_core import DocumentError
            raise DocumentError("单次请求的保守 Token 预留超过文档 TPM 限额；请核实服务商限额后调整，或使用较小的翻译分块。未发送该请求。")
        while True:
            self.control.checkpoint()
            with self.lock:
                delay = max(0, self.cooldown - time.monotonic())
                if self.inflight >= self.limit:
                    delay = max(delay, .05)
                if delay == 0:
                    with self.store.lock, self.store.db:
                        self.store.db.execute("BEGIN IMMEDIATE")
                        now = time.time()
                        self.store.db.execute("DELETE FROM request_quota WHERE started <= ?", (now - 60,))
                        rows = self.store.db.execute("SELECT started,tokens FROM request_quota WHERE scope=? ORDER BY started", (self.scope,)).fetchall()
                        if rows:
                            delay = max(0, rows[-1][0] + max(1, 60 / self.rpm) - now)
                            if len(rows) >= self.rpm or self.tpm and sum(r[1] for r in rows) + tokens > self.tpm:
                                delay = max(delay, rows[0][0] + 60 - now)
                        if delay == 0:
                            identity = uuid.uuid4().hex
                            self.store.db.execute("INSERT INTO request_quota VALUES (?,?,?,?)", (identity, self.scope, now, tokens))
                            self.inflight += 1
                            return identity
            self.wait(min(.1, max(.01, delay)))

    def release(self, identity, actual=None):
        with self.lock:
            self.inflight -= 1
            if actual is not None:
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE request_quota SET tokens=? WHERE id=?", (actual, identity))

    def backoff(self, seconds):
        with self.lock:
            self.cooldown = max(self.cooldown, time.monotonic() + seconds)
            self.limit = 1
            self.successes = 0

    def success(self):
        with self.lock:
            if time.monotonic() < self.cooldown or self.limit >= self.maximum:
                return
            self.successes += 1
            if self.successes >= 3:
                self.limit += 1
                self.successes = 0
