# 浮译 LinguaFloat 

[下载公开稳定版](https://github.com/astatineat2/linguafloat-releases/releases/latest)  · [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md) · [查看完整更新日志](https://github.com/astatineat2/linguafloat-releases/blob/main/CHANGELOG.md)


**面向大学生、科研学习者的 Windows 桌面翻译工具。**

LinguaFloat 是一个桌面翻译工具：翻译游戏屏幕上的外文对话、把你想说的中文转成英文发出去，在任意窗口选中文本即译，以及基于开源组件翻译 PDF、Word 和 PowerPoint 文档并保留原有排版。游戏交流、日常学习和文献翻译，不必再开三个工具来回倒。

## 适合哪些场景

| 使用场景 | 可以怎样使用 |
| --- | --- |
| 外服游戏与跨语言交流 | 框选屏幕对话进行 OCR 翻译；把输入的中文翻译成英文，复制后自行发送。 |
| 课程学习与外文阅读 | 选中文字后用快捷键或右键入口翻译；无法直接取词时，可使用区域 OCR。 |
| 科研资料与文献翻译 | 翻译 PDF、DOCX、PPTX；PDF 可选择保留版式和双语对照，结合原文阅读。 |

## 核心优势

- **兼顾看懂与表达。** 区域翻译帮助理解外文，输入翻译帮助组织回复；目标语言可按需要选择。
- **多种翻译方式集中使用。** 选中、输入、屏幕区域和文件翻译共用供应商配置，日常交流与学习可以接着用。
- **按阅读需要处理文献。** PDF 支持纯译文、双语左右并排或原译交替；支持保留版式，默认保护参考文献，后续可识别的附录继续翻译。
- **供应商与模型可配置。** 支持 Responses、Chat Completions、Anthropic Messages 和 Gemini generateContent，可从 CC Switch / OpenCode 批量导入完整连接配置；实际可用性取决于供应商支持、账户权限及额度。
- **减少重复操作与请求。** 保存常用设置和输出目录，复用匹配的译文缓存；文件任务提供进度、历史及供应商返回的 Token 用量。
- **适合持续使用。** 区域翻译支持暂停、移动和缩放；游戏模式仅保留区域翻译，软件内可查看指南、检查并安装更新。

## 下载与开始使用

当前公开版本：**v0.17.0 build1 · Windows x64**。便携 ZIP 约 501.4 MiB，中文安装包约 371.2 MiB，完整运行文件约 1.23 GiB。完整安装、升级与卸载验收未完成，详见[发布说明](docs/public/发布说明-v0.17.0.md)。自动更新通道暂保留旧版，手动下载本版请使用以下链接。

1. 前往 [v0.17.0 build1 下载页](https://github.com/astatineat2/linguafloat-releases/releases/tag/v0.17.0-build1)，选择完整便携 ZIP 或中文安装程序。
2. 完整解压到有写入权限的目录，运行 `LinguaFloat.exe`。**无需安装 Python**；请保留 `_internal`、`document-runtime` 和更新助手，不要只复制 EXE。
3. 首次进入可配置翻译服务并保存，或跳过后从配置入口设置。保存不会自动请求接口；“获取模型”和实际翻译由用户主动触发。详细步骤见 [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md)。

便携 ZIP 和安装包包含相同完整运行文件，无需同时下载。安装临时空间峰值未实测；更新需另留下载、新版暂存及旧版备份空间。首次 PDF 处理还可能下载缺失的布局模型与字体，未验证完全离线首次使用。

第三方翻译 API 的费用由服务商收取，与下载软件是两回事。首次启动不会自动翻译或上传论文；“测试连接”和主动翻译会发送文字给所选供应商，可能产生费用。屏幕 OCR 在本地运行，后续翻译仍由所选服务处理。

取词和游戏画面兼容性因软件而异；独占全屏或受保护画面可能无法截图。复杂扫描件、图表和公式的译文排版需要检查，不保证所有文件无损还原。当前程序没有 Windows Authenticode 签名；下载及更新校验、网络说明见 [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md)。

## 当前公开版本 · v0.17.0

- 新增单页首次配置、保存或跳过；精简中文安装流程。目录新建命名、完整安装、完成页和卸载验收仍暂停，不能视为全部通过。
- 延续 v0.16.3 及此前已交付修复；正式公开手动下载和完整对应源码，保留旧版签名自动更新通道。完整安装验收未完成，不将公开发布等同于验收通过。

- 修复静态区域长译文引发的重复排版循环；变量修饰线共同保护，修正旋转图文坐标并复用相关正文译文；Now Coding 添加采用公共预设，两组快捷键统一底部保存。验证范围与限制见 `docs/verification-v0.16.3.md`。

- 简化供应商表单、紧凑快捷键、自适应列宽、四行任务信息与就近详情；默认保护参考文献，后续附录继续翻译。修复方程编号误判分栏、参考文献回填重叠和可安全原位替换的旋转图题。验证范围见 `docs/verification-v0.16.2.md`。

- 按 Privacyboard 指南统一浅灰紫工作台，采用逻辑像素字号；文件页固定任务操作、压缩设置区，状态文字与细进度条分开。保留原有配置入口与翻译流程。验证见 `docs/verification-v0.16.1.md`。

- 可隔离的翻译、公式校验及排版失败改为保留局部原文，其余内容正常输出；PDF 使用红框和未翻译标记，Word 使用红色段落边框，PPT 使用红色波浪下划线。
- 完成状态显示真实未翻译数量，输出目录提供脱敏问题日志。失败结果不写入完整译后快照；鉴权、请求故障、取消及输出写入失败仍明确停止。验证范围见 `docs/verification-v0.15.7.md`。

- 修复公式间文字补译遇到不合格片段立即导致整篇失败的问题：保留合格片段，在当前重试预算内定向补译，不删除公式标记或跳过正文。内容修复与网络重试共用预算，只有全部校验通过才回填。
- 空译文、格式、编号及公式错误分别报告。成功修复写回对应缓存，其他缓存保留；仅重新排版不自动补译。没有新增依赖，完整离线重放及限制见 `docs/verification-v0.15.6.md`。

- 修复部分原生 PDF 没有参考文献标题、采用独立小字号编号时被错误拒绝的问题；同时核对连续条目、作者年份与本地版面证据，不自动关闭文末保护。
- 文末识别规则仅适用于证据充分的单栏原生 PDF；扫描件、无编号或不确定分栏仍保守停止。这项识别不新增 API 请求；此前验证见 `docs/verification-v0.15.5.md`。

- 文件翻译在原有请求中携带有上限的原文上下文、章节及相关缩写定义候选；歧义不强制统一，不增加专门的术语提取请求。
- 扫描件排版不再采用隐藏 OCR 层的任意字号；改善识别行末连字符处理和相离文本块的分组。
- 修复原始行对象内的公式底字与上标分离换行，保留原字形和公式边界。
- Office 多格式段落新增标签外文字、空译文及小数／百分比完整性检查，拒绝损坏响应并失效对应缓存。
- 保留递归修复、参考文献、公式、任务控制及 Go 适配。策略更新后旧缓存保留，但不冒充新策略结果；仅重新排版不会自动补发请求。
- 无新增依赖或独立操作入口。工程改进已验证，实际翻译质量提升仍待验证；详见 `docs/verification-v0.15.4.md`。仅本地交付。

## 开源致谢与许可证

LinguaFloat 集成了多个开源项目的能力，感谢这些项目及其贡献者：

- [BabelDOC](https://github.com/funstory-ai/BabelDOC) 与 [PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)：PDF 翻译与版式处理。
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF)：PDF 读写与渲染。
- [RapidOCR](https://github.com/RapidAI/RapidOCR) 与 [ONNX Runtime](https://github.com/microsoft/onnxruntime)：本地文字识别与模型运行。
- [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/)：桌面界面；其他组件详见 [第三方声明](https://github.com/astatineat2/linguafloat-releases/blob/main/THIRD_PARTY_NOTICES.md)。

LinguaFloat 的适用源码按 [AGPL-3.0-only](https://github.com/astatineat2/linguafloat-releases/blob/main/LICENSE) 提供，第三方组件各自保留原有许可证。[开发源码仓库](https://github.com/astatineat2/linguafloat)已按作者确认公开；每个公开版本仍在 [Releases](https://github.com/astatineat2/linguafloat-releases/releases) 中提供与软件匹配的 `corresponding-source.zip` 及必要材料。

普通用户选择完整便携 ZIP 或中文安装程序即可；需要源码时，请选择明确标注的对应源码附件，GitHub 自动生成的 “Source code.zip” 不等同于该附件。
