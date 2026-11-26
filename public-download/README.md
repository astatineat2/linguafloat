# 浮译 LinguaFloat

**面向大学生、科研学习者和外服游戏玩家的 Windows 桌面翻译工具。**

[下载稳定版](https://github.com/astatineat2/linguafloat-releases/releases/latest) · [查看源码](https://github.com/astatineat2/linguafloat) · [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md) · [查看完整更新日志](https://github.com/astatineat2/linguafloat-releases/blob/main/CHANGELOG.md)

旧便携版和 Setup 安装版是不同的分发方式；旧便携版不会自动切换成安装版。已有便携发布和对应说明仍可在历史发布页查阅。

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
- **按阅读需要处理文献。** PDF 支持纯译文、双语左右并排或原译交替；支持保留版式，默认保护参考文献，后续可识别的附录继续翻译。
- **供应商与模型可配置。** 支持 Responses、Chat Completions、Anthropic Messages 与 Gemini generateContent；可手动填写模型或主动获取模型列表，实际可用性取决于供应商、账户及额度。
- **减少重复操作与请求。** 保存常用设置和输出目录，复用匹配的译文缓存；文件任务提供进度、历史及供应商返回的 Token 用量。
- **适合持续使用。** 区域翻译支持暂停、移动和缩放；游戏模式仅保留区域翻译，软件内可查看指南、检查并安装更新。

## 下载与开始使用

自 v0.20.0 起，Windows x64 软件以 Setup 安装程序发布。

1. 前往[稳定版发布页](https://github.com/astatineat2/linguafloat-releases/releases/latest)，选择 `windows-x64-Setup.exe`。
2. Setup 安装后从开始菜单启动。**无需安装 Python**。已使用 Setup 的用户可在「帮助与更新」检查后续安装版；卸载可用开始菜单的「卸载 LinguaFloat」或 Windows 设置。
3. 首次进入可配置翻译服务并保存，或跳过后从配置入口设置。保存不会自动请求接口；“获取模型”或实际翻译由用户主动触发。详细步骤见 [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md)。

请核对同版 `SHA256SUMS.txt`，包大小以发布页为准。解压、更新暂存及旧版备份需要额外空间；首次 PDF 处理还可能下载缺失的布局模型与字体，未验证完全离线首次使用。

第三方翻译 API 的费用由服务商收取，与下载软件是两回事。首次启动不会自动翻译或上传论文；“测试连接”和主动翻译会发送文字给所选供应商，可能产生费用。屏幕 OCR 在本地运行，后续翻译仍由所选服务处理。

取词和游戏画面兼容性因软件而异；独占全屏或受保护画面可能无法截图。复杂扫描件、图表和公式的译文排版需要检查，不保证所有文件无损还原。当前程序没有 Windows Authenticode 签名；下载及更新校验、网络说明见 [使用指南](https://github.com/astatineat2/linguafloat-releases/blob/main/使用指南.md)。

## v0.23.0 更新内容

- 在「译文导出」直接选择已有 PDF，转为可编辑的 Word 或 Markdown，不必重新翻译。扫描、复杂公式和表格仍需对照原件。
- 「译后校对」分开显示本地覆盖、版面检查和可选 AI 语义校对。AI 默认关闭，校对不覆写译文。
- 修复部分双栏参考文献重叠、复制重复及链接位置异常。已有 PDF 不会自动改写。

[查看完整更新日志](https://github.com/astatineat2/linguafloat-releases/blob/main/CHANGELOG.md) · [发布验证与实测范围](https://github.com/astatineat2/linguafloat-releases/blob/main/验证记录.md)

## 开源致谢与许可证

LinguaFloat 集成了多个开源项目的能力，感谢这些项目及其贡献者：

- [BabelDOC](https://github.com/funstory-ai/BabelDOC) 与 [PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)：PDF 翻译与版式处理。
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF)：PDF 读写与渲染。
- [RapidOCR](https://github.com/RapidAI/RapidOCR) 与 [ONNX Runtime](https://github.com/microsoft/onnxruntime)：本地文字识别与模型运行。
- [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/)：桌面界面；其他组件详见 [第三方声明](https://github.com/astatineat2/linguafloat-releases/blob/main/THIRD_PARTY_NOTICES.md)。

LinguaFloat 的适用源码按 [AGPL-3.0-only](https://github.com/astatineat2/linguafloat-releases/blob/main/LICENSE) 提供，第三方组件各自保留原有许可证。[开发源码仓库](https://github.com/astatineat2/linguafloat)已按作者确认公开；已公开版本在 [Releases](https://github.com/astatineat2/linguafloat-releases/releases) 中提供与软件匹配的 `corresponding-source.zip` 及必要材料。

普通用户选择 Setup.exe；需要源码时，请选择明确标注的对应源码附件，GitHub 自动生成的 “Source code.zip” 不等同于该附件。
