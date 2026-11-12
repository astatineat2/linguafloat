import copy
import hashlib
import json
import sqlite3

import pytest

from linguafloat.cc_switch import (
    CCSwitchError, ImportChoice, commit_import, discover_sources, duplicate_targets, read_candidates,
)
from linguafloat.config import AppSettings, ConfigStore, ProviderProfile


def source_config(**overrides):
    data = {"auth": {"OPENAI_API_KEY": "test-secret-only"}, "config": '''
model = "test-model"
model_provider = "custom"
[model_providers.custom]
base_url = "https://example.test/custom/v1/responses"
wire_api = "responses"
requires_openai_auth = true
'''}
    data.update(overrides)
    return data


def database(path, entries=None):
    entries = entries or [("1", "codex", "Demo", source_config(), {}, "")]
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE providers(id TEXT, app_type TEXT,name TEXT,settings_config TEXT,meta TEXT,provider_type TEXT)")
        for pid, app, name, config, meta, kind in entries:
            conn.execute("INSERT INTO providers VALUES(?,?,?,?,?,?)", (pid, app, name, json.dumps(config), json.dumps(meta), kind))
    return path


class Vault:
    def __init__(self):
        self.data = {}
        self.fail_write = 0
        self.calls = 0

    def get_api_key(self, pid, env):
        assert env == ""
        return self.data.get(pid, "")

    def save_api_key(self, pid, key):
        self.calls += 1
        if self.calls == self.fail_write:
            raise RuntimeError("secret backend error")
        self.data[pid] = key

    def delete_api_key(self, pid):
        self.data.pop(pid, None)


def test_real_schema_read_only_all_rows_and_auth_not_oauth(tmp_path):
    path = database(tmp_path / "source.db", [
        ("1", "codex", "One", source_config(), {}, ""),
        ("2", "codex", "Two", source_config(), {}, ""),
        ("3", "claude", "Claude", {"env": {}}, {}, ""),
    ])
    before = hashlib.sha256(path.read_bytes()).digest()
    items = read_candidates(path)
    assert len(items) == 3
    assert sum(not item.blocked for item in items) == 3
    assert not items[0].profile.model and not items[0].secret
    assert items[1].secret == "test-secret-only"
    assert "test-secret-only" not in repr(items)
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_discovery_default_override_and_legacy_json(tmp_path):
    home, appdata, custom = tmp_path / "home", tmp_path / "appdata", tmp_path / "custom"
    (home / ".cc-switch").mkdir(parents=True)
    database(home / ".cc-switch/cc-switch.db")
    custom.mkdir()
    (custom / "config.json").write_text('{"apps":{}}')
    store = appdata / "com.ccswitch.desktop/app_paths.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"app_config_dir_override": str(custom)}))
    paths = discover_sources(home, appdata)
    assert paths == [custom / "config.json", home / ".cc-switch/cc-switch.db"]
    assert discover_sources(tmp_path / "absent", tmp_path / "absent") == []


def test_manual_legacy_json(tmp_path):
    path = tmp_path / "chosen.json"
    path.write_text(json.dumps({"apps": {"codex": {"providers": {"a": {
        "name": "A", "settingsConfig": source_config()}}, "current": "a"}}}))
    assert read_candidates(path)[0].profile.model == "test-model"


@pytest.mark.parametrize("wire,expected,blocked", [
    ('wire_api = "chat"', "chat_completions", False),
    ("", "", False), ('wire_api = "messages"', "", True),
])
def test_protocol_is_explicit_not_model_name(tmp_path, wire, expected, blocked):
    config = source_config()
    config["config"] = config["config"].replace('wire_api = "responses"', wire)
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", config, {}, "")]))[0]
    assert c.profile.api_mode == expected
    assert bool(c.blocked) == blocked


@pytest.mark.parametrize("app,auth,meta,kind", [
    ("openclaw", {}, {}, ""),
    ("codex", {"tokens": {"access_token": "private"}}, {}, ""),
    ("codex", {}, {"requiresOAuth": True}, ""),
    ("codex", {}, {}, "codex_oauth"),
    ("codex", {"auth_mode": "chatgpt", "OPENAI_API_KEY": "fake"}, {}, ""),
])
def test_unsupported_auth_protocols_blocked(tmp_path, app, auth, meta, kind):
    c = read_candidates(database(tmp_path / "s.db", [("1", app, "A", source_config(auth=auth), meta, kind)]))[0]
    assert c.blocked
    assert not c.secret


def test_codex_selected_profile_and_missing_model(tmp_path):
    config = source_config()
    config["config"] = 'profile = "work"\n' + config["config"] + '\n[profiles.work]\nmodel="override-model"\n'
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", config, {}, "")]))[0]
    assert c.profile.model == "override-model"
    config["config"] = config["config"].replace('model = "test-model"', '').replace('model="override-model"', '')
    c = read_candidates(database(tmp_path / "s2.db", [("1", "codex", "A", config, {}, "")]))[0]
    assert c.profile.model == ""


@pytest.mark.parametrize("value", ["${MISSING}", "{env:MISSING}", "%MISSING%", "your_api_key", "********"])
def test_unresolved_placeholders_never_become_keys(tmp_path, value):
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", source_config(auth={"OPENAI_API_KEY": value}), {}, "")]))[0]
    assert not c.secret


@pytest.mark.parametrize("keep", [True, False])
def test_environment_reference_or_vault_value(tmp_path, monkeypatch, keep):
    monkeypatch.setenv("CC_TEST_KEY", "environment-test-secret")
    config = source_config()
    config["config"] += '\nenv_key="CC_TEST_KEY"\n'
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", config, {}, "")]))[0]
    assert c.secret == "environment-test-secret"
    settings, vault = AppSettings(), Vault()
    store = ConfigStore(tmp_path / "app.json")
    commit_import([ImportChoice(c, "add", keep_env=keep)], settings, store, vault)
    p = settings.providers[-1]
    assert p.env_key == ("CC_TEST_KEY" if keep else "")
    assert bool(vault.data) != keep
    assert "environment-test-secret" not in store.path.read_text(encoding="utf-8")


def test_missing_env_does_not_replace_old_auth(tmp_path, monkeypatch):
    monkeypatch.delenv("CC_TEST_MISSING", raising=False)
    config = source_config()
    config["config"] += '\nenv_key="CC_TEST_MISSING"'
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", config, {}, "")]))[0]
    settings, vault = AppSettings(), Vault()
    vault.data["openai"] = "old-key"
    commit_import([ImportChoice(c, "update", "openai")], settings, ConfigStore(tmp_path / "app.json"), vault)
    assert settings.providers[0].env_key == "OPENAI_API_KEY"
    assert vault.data["openai"] == "old-key"


def test_missing_key_update_preserves_parameters_and_old_profile_object(tmp_path):
    c = read_candidates(database(tmp_path / "s.db"))[0]
    c.secret = ""
    settings, vault = AppSettings(), Vault()
    old = settings.providers[0]
    old.document_timeout, old.retries = 460, 4
    snapshot = copy.deepcopy(old)
    vault.data[old.id] = "existing"
    commit_import([ImportChoice(c, "update", old.id)], settings, ConfigStore(tmp_path / "app.json"), vault)
    assert old == snapshot
    assert settings.active_provider == old.id
    assert settings.providers[0].document_timeout == 460
    assert settings.providers[0].retries == 4
    assert vault.data[old.id] == "existing"


def test_multi_import_duplicates_roundtrip_and_no_secrets_on_disk(tmp_path):
    c = read_candidates(database(tmp_path / "s.db"))[0]
    settings, vault = AppSettings(), Vault()
    store = ConfigStore(tmp_path / "app.json")
    report = commit_import([ImportChoice(c, "add"), ImportChoice(c, "add")], settings, store, vault)
    assert sum(result == "成功" for _, result, _ in report) == 2
    assert len({p.id for p in settings.providers}) == 3
    assert len({p.name for p in settings.providers}) == 3
    assert duplicate_targets(c, settings.providers)
    assert store.load().providers == settings.providers
    assert store.load().active_provider == "openai"
    assert "test-secret-only" not in store.path.read_text(encoding="utf-8")


def test_skip_is_zero_writes(tmp_path):
    c = read_candidates(database(tmp_path / "s.db"))[0]
    store, vault = ConfigStore(tmp_path / "app.json"), Vault()
    report = commit_import([ImportChoice(c)], AppSettings(), store, vault)
    assert report[0][1] == "跳过"
    assert not store.path.exists()
    assert vault.calls == 0


@pytest.mark.parametrize("failure", ["vault", "config"])
def test_batch_failure_restores_credentials_and_settings(tmp_path, failure):
    c = read_candidates(database(tmp_path / "s.db"))[0]
    settings, vault = AppSettings(), Vault()
    before = copy.deepcopy(settings)
    vault.data["openai"] = "old-key"
    store = ConfigStore(tmp_path / "app.json")
    store.save(settings)
    raw_before = store.path.read_bytes()
    if failure == "vault":
        vault.fail_write = 2
    else:
        def fail(_):
            raise OSError("secret must not be shown")
        store.save = fail
    with pytest.raises(CCSwitchError) as error:
        commit_import([ImportChoice(c, "update", "openai"), ImportChoice(c, "add")], settings, store, vault)
    assert "secret" not in str(error.value)
    assert settings == before
    assert vault.data == {"openai": "old-key"}
    assert store.path.read_bytes() == raw_before


def test_incomplete_or_conflicting_batch_never_writes(tmp_path):
    c = read_candidates(database(tmp_path / "s.db"))[0]
    settings, vault = AppSettings(), Vault()
    store = ConfigStore(tmp_path / "app.json")
    with pytest.raises(CCSwitchError):
        commit_import([ImportChoice(c, "update", "openai")] * 2, settings, store, vault)
    c.profile.model = ""
    with pytest.raises(CCSwitchError):
        commit_import([ImportChoice(c, "add")], settings, store, vault)
    assert vault.calls == 0


def test_wal_snapshot_and_exclusive_lock(tmp_path):
    path = database(tmp_path / "s.db")
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("UPDATE providers SET name='Committed'")
    writer.commit()
    writer.execute("UPDATE providers SET name='Uncommitted'")
    assert read_candidates(path)[0].profile.name == "Committed"
    writer.rollback()
    writer.execute("PRAGMA journal_mode=DELETE")
    writer.execute("BEGIN EXCLUSIVE")
    with pytest.raises(CCSwitchError, match="独占"):
        read_candidates(path)
    writer.rollback()
    writer.close()


def test_broken_rows_and_unsafe_headers_are_not_echoed(tmp_path):
    config = source_config(config='secret="do-not-echo\n')
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", config, {}, "")]))[0]
    assert c.blocked and "do-not-echo" not in repr(c)
    config = source_config()
    config["config"] += '\nhttp_headers={"x-opencode-session"="other-session"}'
    c = read_candidates(database(tmp_path / "s2.db", [("1", "codex", "A", config, {}, "")]))[0]
    assert c.blocked and "other-session" not in repr(c)


def test_custom_prefix_normalized_only_at_commit(tmp_path):
    c = read_candidates(database(tmp_path / "s.db"))[0]
    settings = AppSettings()
    commit_import([ImportChoice(c, "add")], settings, ConfigStore(tmp_path / "app.json"), Vault())
    assert settings.providers[-1].base_url == "https://example.test/custom/v1"


def test_source_session_is_not_copied_for_go(tmp_path):
    from linguafloat.api_compat import opencode_go_headers
    config = source_config()
    config["config"] = config["config"].replace("https://example.test/custom/v1/responses", "https://opencode.ai/inference/go/openai/v1")
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", config, {"sessionId": "foreign-session"}, "")]))[0]
    assert not c.blocked
    headers = opencode_go_headers(c.profile)
    assert headers["x-opencode-session"] != "foreign-session"
    assert headers["User-Agent"].startswith("LinguaFloat/")


@pytest.mark.parametrize("wire,mode", [("responses", "responses"), ("chat", "chat_completions")])
def test_imported_profile_uses_shared_translator_and_keeps_snapshot(tmp_path, wire, mode):
    from types import SimpleNamespace as NS
    from linguafloat.translation import OpenAITranslator
    config = source_config()
    config["config"] = config["config"].replace('wire_api = "responses"', f'wire_api = "{wire}"')
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", config, {}, "")]))[0]
    settings, vault = AppSettings(), Vault()
    store = ConfigStore(tmp_path / "app.json")
    commit_import([ImportChoice(c, "add")], settings, store, vault)
    calls = []
    def respond(**kwargs):
        calls.append(kwargs)
        return NS(output_text="你好", status="completed", choices=[NS(finish_reason="stop", message=NS(content="你好"))])
    client = NS(responses=NS(create=respond), chat=NS(completions=NS(create=respond)))
    translator = OpenAITranslator("synthetic-only", settings.providers[-1], client=client)
    assert calls == []
    c.profile.model = "changed-model"
    commit_import([ImportChoice(c, "update", settings.providers[-1].id)], settings, store, vault)
    assert translator.translate("Hello") == "你好"
    assert calls[0]["model"] == "test-model"
    assert ("input" if mode == "responses" else "messages") in calls[0]


def test_managed_account_binding_is_not_imported(tmp_path):
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", source_config(),
        {"authBinding": {"source": "managed_account", "accountId": "private"}}, "")]))[0]
    assert c.blocked and not c.secret


def test_environment_placeholder_cannot_be_copied_as_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_PLACEHOLDER", "${UNRESOLVED}")
    config = source_config()
    config["config"] += '\nenv_key="CC_PLACEHOLDER"'
    c = read_candidates(database(tmp_path / "s.db", [("1", "codex", "A", config, {}, "")]))[0]
    assert not c.secret
    with pytest.raises(CCSwitchError, match="未解析"):
        commit_import([ImportChoice(c, "add", keep_env=False)], AppSettings(), ConfigStore(tmp_path / "app.json"), Vault())
