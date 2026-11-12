"""Bounded in-memory CC Switch SQL exchange; never execute SQL on user databases."""
import json
import sqlite3
import time
import tempfile
from pathlib import Path

from linguafloat.cc_sql_schema import SCHEMA, SCHEMA_VERSION

HEADER = "-- CC Switch SQLite 导出"
REQUIRED = {"providers", "provider_endpoints", "mcp_servers", "prompts", "skills", "skill_repos", "settings"}


def import_memory(path):
    from linguafloat.cc_switch import CCSwitchError
    if path.stat().st_size > 64 * 1024 * 1024:
        raise CCSwitchError("SQL 文件超过 64 MiB，请从 CC Switch 导出精简备份或选择数据库。")
    sql = path.read_text(encoding="utf-8-sig")
    if not sql.lstrip().startswith(HEADER):
        raise CCSwitchError("不是带官方标识的 CC Switch SQL 备份。")
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute("PRAGMA max_page_count=32768")
    db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024 * 1024)
    db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20 * 1024 * 1024)
    deadline = time.monotonic() + 8
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    allowed = {sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
               sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
               sqlite3.SQLITE_DELETE, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_REINDEX}

    def authorize(action, first, second, database, trigger):
        if database not in {None, "main"} or trigger is not None:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            return sqlite3.SQLITE_OK if first.lower() in {"user_version", "foreign_keys"} else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (second or "").lower() in {"datetime", "strftime", "coalesce", "ifnull", "lower"} else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    try:
        db.set_authorizer(authorize)
        db.executescript(sql)
        db.set_authorizer(None)
        db.set_progress_handler(None, 0)
        if db.in_transaction:
            raise CCSwitchError("SQL 事务未结束，文件可能被截断。")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED <= tables:
            raise CCSwitchError("SQL 缺少 CC Switch 必需表，文件不完整。")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if not 0 <= version <= SCHEMA_VERSION:
            raise CCSwitchError(f"SQL schema 版本 {version} 尚未验证；当前支持至 {SCHEMA_VERSION}，请勿降级覆盖来源。")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise CCSwitchError("SQL 数据结构完整性检查失败。")
        return db
    except Exception as exc:
        db.close()
        if isinstance(exc, CCSwitchError):
            raise
        raise CCSwitchError("SQL 损坏、过于复杂或含不允许的语句；已拒绝，未改动任何数据库。") from None


def sql_rows(path):
    from linguafloat.cc_switch import CCSwitchError
    db = import_memory(path)
    try:
        columns = {r[1] for r in db.execute("PRAGMA table_info(providers)")}
        if not {"id", "app_type", "name", "settings_config"} <= columns:
            raise CCSwitchError("SQL 供应商表字段不完整。")
        meta = "meta" if "meta" in columns else "'{}'"
        kind = "provider_type" if "provider_type" in columns else "''"
        return db.execute(f"SELECT id,app_type,name,settings_config,{meta},{kind} FROM providers ORDER BY app_type,id LIMIT 2001").fetchall()
    finally:
        db.close()


def export_sql(path, profiles, credentials, include_keys=False):
    """Produce a provider-only recovery database, with the complete observed schema."""
    from linguafloat.config import validate_provider
    import copy
    import tomli_w
    db = sqlite3.connect(":memory:")
    report = []
    try:
        db.executescript(SCHEMA)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        for source in profiles:
            p = copy.deepcopy(source)
            try:
                validate_provider(p)
                key = credentials.get_api_key(p.id, p.env_key) if include_keys else ""
                if p.api_mode in {"responses", "chat_completions"}:
                    app = "codex"
                    provider = {"name": p.name, "base_url": p.base_url,
                                "wire_api": "responses" if p.api_mode == "responses" else "chat", "requires_openai_auth": True}
                    if p.env_key and not include_keys:
                        provider["env_key"] = p.env_key
                    config = {"auth": {"OPENAI_API_KEY": key}, "config": tomli_w.dumps({
                        "model_provider": "linguafloat", "model": p.model, "model_providers": {"linguafloat": provider}})}
                elif p.api_mode == "anthropic":
                    app = "claude"
                    config = {"env": {"ANTHROPIC_BASE_URL": p.base_url, "ANTHROPIC_MODEL": p.model,
                        "ANTHROPIC_AUTH_TOKEN" if p.native_auth == "bearer" else "ANTHROPIC_API_KEY": key}}
                elif p.api_mode == "gemini" and p.native_auth == "api_key":
                    app = "gemini"
                    config = {"env": {"GOOGLE_GEMINI_BASE_URL": p.base_url, "GEMINI_MODEL": p.model, "GEMINI_API_KEY": key}}
                else:
                    report.append((p.name, "不支持", "无法无损映射此认证到 CC Switch 来源类型"))
                    continue
                db.execute("INSERT INTO providers(id,app_type,name,settings_config,meta,is_current) VALUES(?,?,?,?,?,0)",
                           (p.id, app, p.name, json.dumps(config, ensure_ascii=False), "{}"))
                omissions = "仅交换连接字段；并发/超时/重试/翻译设置不迁移"
                report.append((p.name, "已导出", ("含密钥" if key else "不含密钥，需在目标端补全") + "；" + omissions))
            except Exception:
                report.append((p.name, "失败", "字段无效或凭据无法读取；未回显敏感信息"))
        db.commit()
        if not any(state == "已导出" for _, state, _ in report):
            from linguafloat.cc_switch import CCSwitchError
            raise CCSwitchError("没有可导出的兼容配置；未写入 SQL 文件。")
        output = HEADER + "\n-- LinguaFloat provider-only recovery export. Import REPLACES CC Switch database.\n"
        output += f"PRAGMA foreign_keys=OFF;\nPRAGMA user_version={SCHEMA_VERSION};\n"
        output += "\n".join(db.iterdump()) + "\n"
        # No user database is touched; replace target only after generating everything.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(output)
            temporary.replace(path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return report
    finally:
        db.close()
