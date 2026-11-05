# 第三方组件与源码

LinguaFloat 的适用源码按 AGPL-3.0-only 分发。每个第三方组件保留原版权、许可证和相应义务，不能将整个便携包都称作 MIT。

| 组件 | 许可要点 |
| --- | --- |
| BabelDOC / PDFMathTranslate-next | AGPL-3.0，提供匹配的对应源码及修改 |
| PyMuPDF / MuPDF | AGPL；若改用商业许可需另行取得授权 |
| PySide6 / Shiboken / Qt | LGPL/GPL 多许可，核对实际模块、第三方声明及相应源码要求 |
| RapidOCR | Apache-2.0；实际模型权重来源和许可另记录 |
| ONNX Runtime | MIT |
| python-docx / python-pptx | MIT |
| qtawesome | 代码 MIT，打包的图标字体分别保留许可 |
| PyInstaller | GPL 与其打包例外 |
| Python | PSF 及其第三方声明 |

精确安装版本和已收集的许可证见 `LICENSES/app`、`LICENSES/document` 和依赖锁文件。
本版已按实际文件核对依赖版本、OCR 权重、图标字体及匹配源码；清单包含构建环境的部分工具，并不代表每个工具都参与运行。
字体的生成工具许可与字体本身许可需要分开处理。模型源码许可与实际权重许可也需分开核对。

公开发行时，对应源码附件应匹配同版二进制，并含适用的构建材料与第三方源码材料。LinguaFloat [开发源码仓库](https://github.com/astatineat2/linguafloat)已公开；下载者依法获得的再分发权不受额外保密约定限制。
发行页自动生成的“Source code.zip”只属于公开下载仓库；请下载明确标为 `corresponding-source.zip` 的附件。

官方依据：

- https://github.com/funstory-ai/BabelDOC/blob/v0.6.2/LICENSE
- https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/v2.9.0/LICENSE
- https://pymupdf.readthedocs.io/en/latest/about.html
- https://doc.qt.io/qtforpython-6/licenses.html
- https://www.gnu.org/licenses/agpl-3.0.html

素材提供者已于 2026-11-04 确认 `photo/1.png`、`photo/backg.png` 可随软件公开分发；保留原素材。派生 app.ico 同源。其他小图标来自 Material Design Icons，声明见 LICENSES/assets。


图标字体：Codicons 0.0.36（CC BY 4.0，Microsoft）、Elusive Icons 2.0（SIL OFL 1.1，Team Redux / Dave Gandy）、Font Awesome 5.15.4 / 6.7.2（字体 OFL 1.1，Fonticons）、Material Design Icons 5.9.55 / 6.9.96（Apache 2.0，Pictogrammers）、Phosphor 1.3.0（MIT）、Remix Icon 2.5.0（Apache 2.0）。完整声明与文件 SHA-256 见 LICENSES/assets；未修改字体。

打包的 RapidOCR 三个 ONNX 模型按 v3.9.2 default_models.yaml 校验，来源及 Apache 2.0 模型卡留存于 LICENSES/assets。PDF 处理时按需下载的 DocLayout ONNX 模型卡声明 Apache 2.0；布局代码的 AGPL 许可与权重分开记录。BabelDOC 字体/模型下载缓存不随此便携包再分发，首次 PDF 使用可能需要联网取得相应资源。不要将本机已下载缓存的总体积当作每位新用户的必需下载量。

缺少单独 LICENSE 的上游 Python sdist（antlr4、ffmpy、gradio_client、Tencent SDK）补充了上游许可证文本。Qt 原生模块的匹配源归档、第三方声明与构建映射随对应源码包提供。对应源码包不包含开发 Git 历史、发布私钥、个人配置、论文、翻译结果或用户数据库。

## 获取材料

完整便携包包含上述 LICENSES 声明；匹配的上游源码、适用修改和构建材料在同版 `corresponding-source.zip` 中提供。请从[正式 Releases](https://github.com/astatineat2/linguafloat-releases/releases)选择对应版本，不要用下载仓库自动生成的源码 ZIP 替代。[许可证](https://github.com/astatineat2/linguafloat-releases/blob/main/LICENSE) · [构建说明](https://github.com/astatineat2/linguafloat-releases/blob/main/开发与构建.md)。
