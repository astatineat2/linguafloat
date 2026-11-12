import copy
import json
import sqlite3
from pathlib import Path

import httpx
import openai
import pytest

from linguafloat.cc_switch import Candidate, CCSwitchError, import_category, read_candidates
from linguafloat.cc_sql import HEADER, export_sql, import_memory, sql_rows
from linguafloat.config import AppSettings, ConfigStore, ProviderProfile
from linguafloat.opencode_import import discover_opencode, load_jsonc, parse_provider, read_opencode
from linguafloat.api_compat import ApiAdapter, ApiResponseError, token_usage
from linguafloat.translation import OpenAITranslator


class Vault:
    def __init__(self):
        self.data = {}
    def get_api_key(self, pid, env=""):
        import os
        return os.environ.get(env) or self.data.get(pid, "")
    def save_api_key(self, pid, key):
        if key == "fail-vault":
            raise RuntimeError("synthetic private detail")
        self.data[pid] = key
    def delete_api_key(self, pid):
        self.data.pop(pid, None)


def item(pid="a", **options):
    return Candidate("codex", pid, ProviderProfile(pid, "Demo", "https://example.test/custom/v1", "model", env_key="", **options), "synthetic-key")


def test_batch_partial_success_no_activation_no_overwrite_idempotent(tmp_path):
    settings, vault, store = AppSettings(), Vault(), ConfigStore(tmp_path / "settings.json")
    good, bad, missing = item(), item("bad"), item("missing")
    bad.secret = "fail-vault"
    missing.profile.model = ""
    result = import_category([bad, missing, good], settings, store, vault)
    assert [x[1] for x in result] == ["失败", "需要补全", "成功"]
    assert settings.active_provider == "openai"
    assert len(settings.providers) == 2
    result = import_category([good], settings, store, vault)
    assert result[0][1] == "重复跳过"
    changed = copy.deepcopy(good)
    changed.secret = "new-synthetic-key"
    assert import_category([changed], settings, store, vault)[0][1] == "成功"
    assert len(settings.providers) == 3
    assert vault.data[good.profile.id] == "synthetic-key"
    assert "CC Switch" in settings.providers[-1].name
    assert import_category([changed], settings, store, vault)[0][1] == "重复跳过"
    assert store.load().providers == settings.providers
    assert "synthetic-key" not in store.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode,auth", [("responses", "api_key"), ("chat_completions", "api_key"),
    ("anthropic", "api_key"), ("anthropic", "bearer"), ("gemini", "api_key")])
@pytest.mark.parametrize("include", [False, True])
def test_sql_exchange_synthetic_roundtrip_and_no_unrelated_records(tmp_path, mode, auth, include):
    p = item(api_mode=mode, native_auth=auth).profile
    v = Vault()
    v.data[p.id] = "synthetic-secret"
    path = tmp_path / "exchange.sql"
    export_sql(path, [p], v, include)
    assert ("synthetic-secret" in path.read_text(encoding="utf-8")) == include
    candidates = read_candidates(path)
    assert len(candidates) == 1
    actual = candidates[0]
    assert actual.profile.model == p.model
    assert actual.profile.api_mode == mode
    assert actual.profile.native_auth == auth
    assert bool(actual.secret) == include
    db = import_memory(path)
    assert db.execute("SELECT count(*) FROM mcp_servers").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM proxy_request_logs").fetchone()[0] == 0
    assert db.execute("SELECT sum(is_current) FROM providers").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("suffix", [
    "ATTACH DATABASE 'external.db' AS x;", "VACUUM INTO 'external.db';", "SELECT load_extension('extension');",
    "PRAGMA writable_schema=ON;", "PRAGMA temp_store_directory='.';", "CREATE VIRTUAL TABLE x USING fts5(data);",
    "CREATE TRIGGER t AFTER INSERT ON providers BEGIN DELETE FROM providers; END;", "SELECT randomblob(1000000000);",
    "BEGIN TRANSACTION;", "PRAGMA user_version=999;",
])
def test_sql_rejects_escape_dangerous_or_truncated_input(tmp_path, suffix):
    path = tmp_path / "input.sql"
    export_sql(path, [item().profile], Vault())
    path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")
    with pytest.raises(CCSwitchError):
        sql_rows(path)
    assert not (tmp_path / "external.db").exists()


def test_sql_wrong_header_and_missing_schema(tmp_path):
    path = tmp_path / "x.sql"
    for data in ["CREATE TABLE providers(id TEXT);", HEADER + "\nPRAGMA user_version=20;"]:
        path.write_text(data, encoding="utf-8")
        with pytest.raises(CCSwitchError):
            sql_rows(path)


def test_jsonc_comments_env_multimodel_and_oauth(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_KEY_TEST", "env-synthetic-key")
    path = tmp_path / "opencode.jsonc"
    path.write_text('''{
      // no commands are evaluated
      "provider": {"relay": {"npm": "@ai-sdk/openai-compatible",
        "options": {"baseURL":"https://example.test/v1", "apiKey":"{env:OC_KEY_TEST}"},
        "models": {"short":{}, "long":{"id":"long-actual"}},},
        "oauth": {"npm":"@ai-sdk/anthropic","options":{"baseURL":"https://other.test"},"models":{"c":{}}}},
      "plugin":["DO-NOT-EXECUTE"],
    }''', encoding="utf-8")
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"oauth": {"type":"oauth", "access":"PRIVATE"}}))
    entries = read_opencode(path, auth)
    assert len(entries) == 3
    assert entries[0].secret == "env-synthetic-key"
    assert entries[0].profile.env_key == "OC_KEY_TEST"
    assert entries[1].profile.model == "long-actual"
    assert entries[2].blocked and not entries[2].secret
    assert "PRIVATE" not in repr(entries)


def test_opencode_unknown_package_model_missing_and_file_reference():
    c = parse_provider("custom", {"options":{"apiKey":"{file:../../secret}"}, "npm":"untrusted-plugin"})[0]
    assert c.blocked and not c.secret and not c.profile.model
    c = parse_provider("deepseek", {}, default_model="explicit-model")[0]
    assert c.profile.api_mode == "chat_completions"
    assert c.profile.model == "explicit-model"
    assert c.profile.base_url == "https://api.deepseek.com"


def test_opencode_v2_explicit_package_and_settings():
    entries = parse_provider("custom", {"package":"@opencode/ai/providers/openai/chat",
        "settings":{"baseURL":"https://example.test/custom", "apiKey":"synthetic"},
        "models":{"alias":{"modelID":"actual"}}})
    assert entries[0].profile.model == "actual"
    assert entries[0].profile.api_mode == "chat_completions"


def test_opencode_discovery_override(tmp_path, monkeypatch):
    root = tmp_path / "config"
    root.mkdir()
    file = root / "opencode.jsonc"
    file.write_text('{}')
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(root))
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    assert discover_opencode(tmp_path) == [file]


@pytest.mark.parametrize("mode", ["anthropic", "gemini"])
@pytest.mark.parametrize("auth", ["api_key", "bearer"])
def test_native_wire_translation_usage_and_auth(mode, auth):
    calls = []
    body = ({"content":[{"type":"text","text":"你好"}],"stop_reason":"end_turn","usage":{"input_tokens":4,"output_tokens":2}}
            if mode == "anthropic" else {"candidates":[{"finishReason":"STOP","content":{"parts":[{"text":"hidden thinking","thought":True},{"text":"你好"}]}}],
                "usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":2,"thoughtsTokenCount":1,"totalTokenCount":7}})
    def handle(req):
        calls.append(req)
        return httpx.Response(200, json=body, headers={"x-request-id":"native-001"})
    p = ProviderProfile("p", "native", "https://example.test/custom", "test-model", mode, native_auth=auth)
    client = openai.OpenAI(api_key="synthetic", base_url=p.base_url, max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    translator = OpenAITranslator("synthetic", p, client=client)
    assert translator.translate("Hello") == "你好"
    assert translator.last_result.request_id == "native-001"
    request = calls[0]
    payload = json.loads(request.content)
    if mode == "anthropic":
        assert str(request.url) == "https://example.test/custom/v1/messages"
        assert payload["max_tokens"] == 4096 and payload["system"]
    else:
        assert str(request.url) == "https://example.test/custom/v1beta/models/test-model:generateContent"
        assert payload["systemInstruction"]
    if auth == "bearer":
        assert request.headers["Authorization"] == "Bearer synthetic"
    else:
        assert "Authorization" not in request.headers
        assert request.headers["x-api-key" if mode == "anthropic" else "x-goog-api-key"] == "synthetic"
    assert token_usage(translator.last_result.raw, mode) == ((4,2,6) if mode == "anthropic" else (4,3,7))
    client.close()


@pytest.mark.parametrize("mode,body", [
    ("anthropic", {"content":[],"stop_reason":"end_turn"}),
    ("anthropic", {"content":[{"type":"text","text":"partial"}],"stop_reason":"max_tokens"}),
    ("gemini", {"candidates":[]}),
    ("gemini", {"candidates":[{"finishReason":"MAX_TOKENS","content":{"parts":[{"text":"partial"}]}}]}),
])
def test_native_empty_and_truncation_rejected(mode, body):
    p = ProviderProfile("p","P","https://example.test", "model",mode)
    client = openai.OpenAI(api_key="synthetic", base_url=p.base_url,max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))))
    with pytest.raises(ApiResponseError):
        ApiAdapter(client,p).request([{"role":"user","content":"Hello"}])
    client.close()


@pytest.mark.parametrize("mode", ["anthropic", "gemini"])
def test_native_document_translation_shares_adapter(tmp_path, mode):
    from dataclasses import asdict
    from linguafloat.document_core import DocumentJob, DocumentTranslator, JobControl, JobStore
    p = ProviderProfile("p","P","https://example.test/v1" if mode=="anthropic" else "https://example.test/v1beta", "model", mode, retries=0)
    body = {"content":[{"type":"text","text":"你好"}],"stop_reason":"end_turn"} if mode=="anthropic" else {"candidates":[{"finishReason":"STOP","content":{"parts":[{"text":"你好"}]}}]}
    calls=[]
    def handle(r):
        calls.append(str(r.url))
        return httpx.Response(200,json=body)
    client=openai.OpenAI(api_key="synthetic",base_url=p.base_url,max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    job=DocumentJob(str(tmp_path/'source.pdf'), str(tmp_path/'out'), asdict(p))
    store=JobStore(tmp_path/'jobs.db')
    tr=DocumentTranslator(job,'synthetic',store,JobControl(),lambda e:None,client)
    assert tr.translate('Hello') == '你好'
    assert tr.translate('Hello') == '你好'
    assert len(calls)==1 and '/v1/v1' not in calls[0]
    store.close();client.close()


@pytest.mark.parametrize("mode", ["anthropic", "gemini"])
@pytest.mark.parametrize("status", [400, 401, 403, 429, "timeout"])
def test_native_failure_is_diagnosed_redacted_and_not_retried(mode, status):
    from linguafloat.translation import TranslationError
    calls = []
    def handle(request):
        calls.append(request)
        if status == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(status, json={"error": {"type": "synthetic_error", "message": "key synthetic-private"}},
                              headers={"x-request-id": "failure-001"})
    p = ProviderProfile("p", "P", "https://example.test", "model", mode)
    with openai.OpenAI(api_key="synthetic-private", base_url=p.base_url, max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle))) as client:
        with pytest.raises(TranslationError) as caught:
            OpenAITranslator("synthetic-private", p, client=client).translate("Hello")
    assert len(calls) == 1
    assert "synthetic-private" not in str(caught.value)
    assert ("超时" if status == "timeout" else str(status)) in str(caught.value)


@pytest.mark.parametrize("mode", ["anthropic", "gemini"])
@pytest.mark.parametrize("cancel", [False, True])
def test_native_pending_pause_cancel_and_cache_only(tmp_path, mode, cancel):
    import threading
    import time
    from dataclasses import asdict
    from linguafloat.document_core import Cancelled, DocumentError, DocumentJob, DocumentTranslator, JobControl, JobStore
    p = ProviderProfile("p", "P", "https://example.test", "original-model", mode, retries=0)
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    results, calls = [], []
    body = ({"content": [{"type": "text", "text": "你好"}], "stop_reason": "end_turn"} if mode == "anthropic"
            else {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "你好"}]}}]})
    def handle(r):
        calls.append(r)
        started.set()
        assert release.wait(3)
        return httpx.Response(200, json=body)
    with openai.OpenAI(api_key="synthetic", base_url=p.base_url, max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle))) as client:
        job = DocumentJob(str(tmp_path/'a.pdf'), str(tmp_path/'out'), asdict(p))
        control, store = JobControl(), JobStore(tmp_path/'cache.db')
        tr = DocumentTranslator(job, "synthetic", store, control, lambda e: None, client)
        def run():
            try:
                results.append(tr.translate("Hello"))
            except Cancelled:
                results.append("cancelled")
            finally:
                done.set()
        worker = threading.Thread(target=run)
        worker.start()
        try:
            assert started.wait(2)
            job.provider["model"] = "changed-after-start"
            control.paused.set()
            release.set()
            deadline = time.monotonic() + 2
            while store.db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0 and time.monotonic() < deadline:
                time.sleep(.01)
            assert not done.is_set()
            assert store.db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 1
            if cancel:
                control.cancelled.set()
            else:
                control.paused.clear()
            assert done.wait(2)
            assert results == (["cancelled"] if cancel else ["你好"])
            assert "original-model" in (calls[0].content.decode() if mode == "anthropic" else str(calls[0].url))
            job.provider = asdict(p)
            job.relayout_only = True
            fresh = DocumentTranslator(job, "synthetic", store, JobControl(), lambda e: None, client)
            assert fresh.translate("Hello") == "你好"
            with pytest.raises(DocumentError):
                fresh.translate("Missing cache")
            assert len(calls) == 1
        finally:
            control.cancelled.set(); release.set(); worker.join(3); store.close()


def test_open_code_native_precedence_and_bad_entry_does_not_block(tmp_path):
    path = tmp_path / "opencode.json"
    path.write_text(json.dumps({"provider": {"deepseek": {"models": {"old": {}}}, "bad": 12},
                                "providers": {"deepseek": {"models": {"new": {}}}}}))
    entries = read_opencode(path, tmp_path / "no-auth")
    assert entries[0].profile.model == "new"
    assert entries[1].blocked


@pytest.mark.parametrize("value", ["${CC_NATIVE_TEST}", "{env:CC_NATIVE_TEST}", "%CC_NATIVE_TEST%"])
def test_claude_env_reference_is_preserved_without_plaintext_config(tmp_path, monkeypatch, value):
    monkeypatch.setenv("CC_NATIVE_TEST", "synthetic-env-key")
    file = tmp_path / "source.json"
    file.write_text(json.dumps({"apps": {"claude": {"providers": {"id": {"name": "Native", "settingsConfig": {
        "env": {"ANTHROPIC_MODEL": "model", "ANTHROPIC_API_KEY": value}}}}}}}))
    c = read_candidates(file)[0]
    assert c.profile.env_key == "CC_NATIVE_TEST" and c.secret == "synthetic-env-key"
    vault, store, settings = Vault(), ConfigStore(tmp_path/'app.json'), AppSettings()
    assert import_category([c], settings, store, vault)[0][1] == "成功"
    assert store.load().providers[-1].env_key == "CC_NATIVE_TEST"
    assert not vault.data
    assert "synthetic-env-key" not in store.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["anthropic", "gemini"])
def test_native_model_list_uses_correct_auth_and_keeps_manual_fallback(mode, monkeypatch):
    import linguafloat.provider_tools as module
    captured = []
    def handle(req):
        assert req.url.path.endswith("/models")
        assert "Authorization" not in req.headers
        return httpx.Response(200, json={"data": [{"id": "native-model"}]} if mode == "anthropic"
                              else {"models": [{"name": "models/native-model"}]})
    factory = openai.OpenAI
    monkeypatch.setattr(module, "OpenAI", lambda **kw: factory(**kw,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    task = module.ProviderTask(1, ProviderProfile("p", "P", "https://example.test", "manual", mode), "synthetic", "models")
    task.signals.finished.connect(lambda *args: captured.append(args))
    task.run()
    assert captured[0][1:] == (["native-model"], "")
