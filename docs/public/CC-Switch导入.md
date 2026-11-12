# 批量导入 CC Switch / OpenCode 配置

## 配置管理与数据管理

“配置设置”直接展示全部供应商卡片，提供搜索、编辑、启用和更多操作；配置来源信息仍保留，取消了分类导航。顶部“数据管理”按钮与“添加供应商”并排。

1. 点击右上方“数据管理”，在 SQL 卡片点击“选择 SQL 文件”，选择 CC Switch 官方导出的 .sql 文件。
2. 直接批量导入文件内所有类别的兼容供应商，不用先选类别或逐条勾选。相同连接和认证跳过，同名不同内容另存并标注来源。
3. 结果显示实际新增、重复跳过、待补全等数量。“展开详情”可查看原因，缺失项点击“补全”，完成后单独导入；不支持的认证不会强行转换。尚未补全的来源项没有写入正式配置，关闭程序后可再次导入原文件。
4. 点击“返回全部配置”查看卡片，原搜索和滚动位置保留；点击编辑图标打开独立窗口，保存后仍需明确点击“使用此配置”。浏览和编辑不会自动启用。
5. 本机 CC Switch / OpenCode 自动读取和独立文件读取入口已移除。请使用 CC Switch 官方导出的 SQL 文件。
6. 手动添加和 OpenCode 官方服务快捷配置仍在“添加供应商”菜单。

导入不调用接口，不代表连接成功。“测试连接”和翻译会发送请求并可能收费。已启动的翻译任务继续使用原快照。密钥默认隐藏并保存至 Windows 凭据管理器；环境变量引用保留引用，不将值写入普通配置。

## CC Switch 支持范围

实机版本 4.0.4，SQLite schema 20。真实数据库和用户导出的 SQL 均只读验证，8 条配置中 5 条连接完整、3 条待补全；没有进行真实供应商调用。旧版 apps/providers JSON 以构造数据测试。

| 来源应用 | 可映射字段与协议 | 限制 |
| --- | --- | --- |
| Codex | TOML 中明确的 wire_api=responses/chat、base_url、profile/root 模型、API Key / env_key | ChatGPT 登录、托管账号和额外请求头不转换；缺失 wire_api 需补全 |
| Claude Code / Claude Desktop | ANTHROPIC_BASE_URL、ANTHROPIC_MODEL、API_KEY 或 AUTH_TOKEN；原生 Messages，明确 apiFormat 可映射 OpenAI 协议 | 不推断默认模型，不转换 Bedrock/Vertex/OAuth |
| Gemini CLI | GOOGLE_GEMINI_BASE_URL、GEMINI_MODEL、GEMINI_API_KEY / GOOGLE_API_KEY；generateContent | 服务账号、Vertex 登录不转换 |
| OpenClaw | 显式 api、baseUrl、apiKey、models；每个显式模型独立导入 | 自定义请求头和非 API Key 认证不转换 |
| CC Switch 的 OpenCode 条目 | 明确协议包、options/settings、models | 同下节 OpenCode 限制 |

本版仅通过 SQL 交换供应商配置，不读取或修改本机客户端数据库、当前供应商或运行状态。

## CC Switch SQL 双向交换

“选择 SQL 文件”可读取官方 SQL，仅在受限内存数据库中处理，阻止外部数据库、扩展、触发器、文件写入及不允许的语句；不对两款软件的正式数据库执行脚本。文件上限 64 MiB，超大或未知新版本明确拒绝。

“导出 SQL 备份”导出 LinguaFloat 全部兼容配置和 4.0.4/schema 20 必需结构，不带历史、请求日志、MCP、脚本等数据。

**CC Switch 官方 SQL 导入是整库恢复，不是合并供应商。** 导入这个文件会替换其原数据；务必先用 CC Switch 导出完整备份，并优先在独立环境验证。导出窗口会明确提示。当前已做官方源码规则与结构核对、自身隔离往返测试；**尚未完成官方程序的隔离恢复验收，不宣称官方导入已实测通过**。

- 默认不含明文密钥，需要在目标软件补全；Codex 环境变量引用可保留，但目标环境也需存在。
- 主动勾选“包含 API Key”才读取凭据写入 SQL；此文件含敏感信息，不应上传 GitHub或发送给无关人员。
- Responses/Chat 导出为 Codex，Anthropic 导出为 Claude，普通 API Key Gemini 导出为 Gemini CLI。Gemini Bearer 不能完整映射，明确跳过。
- 并发、超时、重试、翻译提示词及本软件会话管理不交换。配置能被导入不保证目标 CLI 版本支持全部协议或具有模型权限。

## OpenCode 客户端与官方服务

OpenCode 客户端直接读取入口已移除；历史导入的供应商配置保留。CC Switch SQL 中的 OpenCode 类别仍按声明字段映射。

“添加供应商 → OpenCode 官方服务”是另一个入口：主动选择按量或 Go，填准确模型和 API Key。按量预填官方 OpenAI / Anthropic / Google 端点及 Bearer 认证；Go 仅保留本机已有 OpenAI 协议接入。不会自动切换计费方案，也不会借用其他客户端会话。支持协议不代表所有模型、账户都可用。

## 原生协议与保护

四种协议共用选中、输入、区域、连接测试和文件翻译的适配层。Anthropic 要求 max_tokens，未设置上限时使用 4096；Gemini 未设置上限时不发送长度参数。空文本、截断、工具调用和安全过滤不作为完整译文缓存。未知用量保留未知，错误隐藏密钥和请求正文。

导入使用逐条保存与凭据补偿回退，单项失败继续其他项；无法承诺断电或凭据服务失效时跨存储原子性。缓存和历史不清空。

## 官方依据

- [CC Switch 4.0.4 SQL 导出、导入与恢复](https://github.com/farion1231/cc-switch/blob/v4.0.4/src-tauri/src/database/backup.rs)
- [CC Switch 4.0.4 schema](https://github.com/farion1231/cc-switch/blob/v4.0.4/src-tauri/src/database/schema.rs)
- [配置目录](https://github.com/farion1231/cc-switch/blob/v4.0.4/src-tauri/src/config.rs) · [目录覆盖](https://github.com/farion1231/cc-switch/blob/v4.0.4/src-tauri/src/app_store.rs)
- [OpenCode 配置](https://opencode.ai/docs/config) · [供应商](https://opencode.ai/docs/providers) · [官方推理接口](https://opencode.ai/v2/docs/console/api/inference/)
- [OpenCode 配置归一化源码](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/normalize.ts)（开发分支会变化）
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages) · [Gemini generateContent](https://ai.google.dev/api/generate-content)

导入器独立实现，复用现有网络依赖；SQL schema 结构声明与 CC Switch MIT 许可证保留于便携包第三方材料中。
