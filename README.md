# 浮译 LinguaFloat

**面向大学生、科研学习者和外服游戏玩家的 Windows 桌面翻译工具。**

[下载最新稳定版](https://github.com/astatineat2/linguafloat-releases/releases/latest) · [查看源码](https://github.com/astatineat2/linguafloat) · [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md) · [查看完整更新日志](https://github.com/astatineat2/linguafloat-releases/blob/main/CHANGELOG.md)

## 为什么做 LinguaFloat

LinguaFloat 最初是为了玩外服游戏时，看懂屏幕上的外文对话，也能把自己想说的中文翻译成英文，方便与其他玩家交流。后来随着学习和科研需求增加，它逐步加入选中文字翻译，以及基于开源组件的 PDF、Word 和 PowerPoint 文件翻译。

项目在 AI 编程辅助下持续迭代，希望把游戏交流、日常学习与文献阅读中的翻译需求，放进同一个桌面工具中，减少来回切换和重复操作。

## 适合哪些场景

| 使用场景 | 可以怎样使用 |
| --- | --- |
| 外服游戏与跨语言交流 | 框选屏幕对话进行 OCR 翻译；把输入的中文翻译成英文，复制后自行发送。 |
| 课程学习与外文阅读 | 选中文字后用快捷键或右键入口翻译；无法直接取词时，可使用区域 OCR。 |
| 科研资料与文献查阅 | 翻译 PDF、DOCX、PPTX；PDF 可选择保留版式和双语对照，结合原文阅读。 |

## 核心优势

- **兼顾看懂与表达。** 区域翻译帮助理解外文，输入翻译帮助组织回复；目标语言可按需要选择。
- **多种翻译方式集中使用。** 选中、输入、屏幕区域和文件翻译共用供应商配置，日常交流与学习可以接着用。
- **按阅读需要处理文献。** PDF 支持纯译文、双语左右并排或原译交替；可选择保留版式，或保留参考文献及后续原文。
- **供应商与模型可配置。** 支持 Responses API 和 Chat Completions，可填写模型名称或获取模型列表；实际可用性取决于供应商支持、账户权限及额度。
- **减少重复操作与请求。** 保存常用设置和输出目录，复用匹配的译文缓存；文件任务提供进度、历史及供应商返回的 Token 用量。
- **适合持续使用。** 区域翻译支持暂停、移动和缩放；游戏模式仅保留区域翻译，软件内可查看指南、检查并安装更新。

## 下载与开始使用

当前稳定版：**v0.11.1 · Windows 11 x64**。

1. 前往 [最新稳定版下载页](https://github.com/astatineat2/linguafloat-releases/releases/latest)，下载完整的 `LinguaFloat-v版本号-win-x64.zip`。
2. 完整解压到有写入权限的目录，运行 `LinguaFloat.exe`。**无需安装 Python**；请保留 `_internal`、`document-runtime` 和更新助手，不要只复制 EXE。
3. 在“配置设置”填写自己的供应商、接口、模型和密钥，测试并保存，再选择“使用此配置”。详细步骤见 [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md)。

当前软件 ZIP 约 **492 MiB**，解压后约 **1.21 GiB**；更新需另留下载、新版暂存及旧版备份空间，建议额外预留至少 2 GiB。首次 PDF 处理还可能下载缺失的布局模型与字体。

第三方翻译 API 的费用由服务商收取，与下载软件是两回事。首次启动不会自动翻译或上传论文；“测试连接”和主动翻译会发送文字给所选供应商，可能产生费用。屏幕 OCR 在本地运行，后续翻译仍由所选服务处理。

取词和游戏画面兼容性因软件而异；独占全屏或受保护画面可能无法截图。复杂扫描件、图表和公式的译文排版需要检查，不保证所有文件无损还原。当前程序没有 Windows Authenticode 签名；下载及更新校验、网络说明见 [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md)。

## 最新版本 · v0.11.1

- 修复 GitHub API 限流时无法检查更新：改用同一公开仓库的签名更新元数据，并保留版本及完整性校验。
- API 退避期间避免重复请求，不在软件中内置 GitHub 访问凭据。

[查看完整更新日志](https://github.com/astatineat2/linguafloat-releases/blob/main/CHANGELOG.md) · [发布验证与实测范围](https://github.com/astatineat2/linguafloat-releases/blob/main/验证记录.md)

## 开源致谢与许可证

LinguaFloat 集成了多个开源项目的能力，感谢这些项目及其贡献者：

- [BabelDOC](https://github.com/funstory-ai/BabelDOC) 与 [PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)：PDF 翻译与版式处理。
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF)：PDF 读写与渲染。
- [RapidOCR](https://github.com/RapidAI/RapidOCR) 与 [ONNX Runtime](https://github.com/microsoft/onnxruntime)：本地文字识别与模型运行。
- [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/)：桌面界面；其他组件详见 [第三方声明](https://github.com/astatineat2/linguafloat-releases/blob/main/THIRD_PARTY_NOTICES.md)。

LinguaFloat 的适用源码按 [AGPL-3.0-only](https://github.com/astatineat2/linguafloat-releases/blob/main/LICENSE) 提供，第三方组件各自保留原有许可证。[开发源码仓库](https://github.com/astatineat2/linguafloat)已公开，可查看代码、测试和构建说明；每个公开版本仍在 [Releases](https://github.com/astatineat2/linguafloat-releases/releases) 中提供与软件匹配的 `corresponding-source.zip` 及必要材料。

普通用户只需下载完整 `win-x64.zip`；需要源码时，请选择明确标注的对应源码附件，GitHub 自动生成的 “Source code.zip” 不等同于该附件。
