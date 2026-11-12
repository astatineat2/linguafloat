# OpenCode Go 接入

v0.11.2 是仅本地交付的修复版，未发布 GitHub 更新。

## 会话修复

对官方 `https://opencode.ai/inference/go/openai/v1` 及已有旧 Go 地址 `https://opencode.ai/zen/go/v1`，软件自动发送自身 `LinguaFloat/版本号` 客户端标识和 `x-opencode-session`。不用手动填写会话，不借用其他客户端身份。

短文本任务各有独立会话。同一文档任务的分块、并发、重试复用会话；从相同任务 ID 恢复时复用，重新创建任务时使用新会话。会话不包含密钥、论文路径或正文，不进入译文缓存匹配范围。模型列表与各翻译入口都覆盖。

会话修复不代表所有模型同时支持 Responses 与 Chat Completions。软件保持用户保存的接口、模型、地址、思考设置和重试参数，不擅自切换计费方案。

## 此前已实测的 DeepSeek 设置

- 地址：`https://opencode.ai/inference/go/openai/v1`
- 模型：`deepseek-v4.1-flash`
- 接口：Chat Completions
- 指令角色：System
- Chat 输出长度参数：`max_tokens`
- 思考开关：测试两页论文时明确选择“DeepSeek：关闭思考”，输出上限 2048；它是测试参数，不是新版自动更改的默认值。

两页真实测试通过独立 Go 客户端接入已安装文档后台，完成 9 次请求，但首页作者/单位排版仍有缺陷；不代表新版 GUI、整篇文件或所有图表已真实验收。新版验证记录区分自动化测试与此前真实测试。

当前已保存的 `opencode` 配置仍可能是旧地址与 Responses。修复不会悄悄改为 Chat，也不会启用其他未启用配置。使用以上已验证参数需在配置设置中选择对应配置或自行修改、保存并启用；启用前核对密钥、账户额度和服务限制。

Go 官方文档要求客户端发送自身标识及每个会话稳定 ID，并说明主要面向编程代理流量。软件如实标识自身，不通过伪造身份绕过服务限制；翻译用途和模型权限仍受供应商规则约束。

依据：[Go 使用要求](https://opencode.ai/v2/docs/console/go/#where-can-i-use-it)、[推理接口](https://opencode.ai/v2/docs/console/api/inference)、[DeepSeek 思考参数](https://api-docs.deepseek.com/guides/thinking_mode/)。
