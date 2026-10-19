# 转换取舍与验证依据

更新日期：2026-10-19。

本文维护实现取舍、样本适用范围及报告定位；当前能力与验证边界见 [README](../../README.md)，未完成工作见 [TODO](../../TODO.md)，操作见 [质量检查](../../tests/common/README.md)，机制见 [转换架构](../../specs/0002-conversion-architecture.md)。本地报告位于不随 Git 分发的 `var/quality/`，缺失时需按质量检查说明准备原件和环境重建。

## 报告定位与适用范围

报告中的源码、检查器、样本和产物哈希用于确认验证对象；报告只支持对应版本与样本的结论，不能直接继承为当前版本验收。具体环境、命令和验证时间保留在报告内。

| 证据目录（相对 `var/quality/`） | 报告与适用范围 |
| --- | --- |
| `checkpoints/phase-01-2026-10-16-final/` | `checkpoint.json`、`installed-source-hashes.json`、`artifacts.json` 定位 EPUB / HTML / DOCX 的源码、安装与产物；`real-epub/full-book.json` 和 `visual-review.json` 记录整书源对照及代表页观察，不能代表所有文件与逐页布局 |
| `url-phase02/` | `checkpoint.json`、`fixture-export.json`、`real-replay.json` 定位固定网页、入口/输出与离线重放；`real-fetch.json` 保留来源快照身份，候选取舍及真实样本边界见下文 |
| `pdf-phase03/` | `checkpoint.json`、`classification.json`、`installed-report.json` 支持逐页分类与 OCR 协议检查；`ocr-environment.json` 记录服务连通性依据，不证明真实识别质量或页级组合 |
| `markdown-import/` | `checkpoint.json` 核对源码、wheel 与源码外安装；`visual-review.json` 记录独立 Markdown 输入经共同正文/Writer 的代表页结果，不证明真实 OCR 效果或 PDF/图片 Reader 完整切换 |
| `resource-cleanup/` | `report.json` 保存已删除旧资源清单及保留字体哈希，用于核对删除边界，不替代后续完整打包与安装检查 |

## 实现取舍

| 依据 | 支持的结论与限制 |
| --- | --- |
| 旧实现基线 `fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4`；[公开报告](../../tests/common/baselines/fc5b9a6.json)，完整报告 `var/quality/baseline-final/` | 58 个组合中 43 个完成转换、6 个异常、9 个 OCR 组合未运行。复现无目录 EPUB 异常、章界重复、导航断链、表格跨度/图片丢失、HTML 下载路径、URL 来源路径及混合 PDF 检测问题；没有全格式赢家证据 |
| DOCX 独立结构对照 `var/quality/docx-before/checks.json` | 旧实现有 13 项失败，支持保留 Mammoth 语义 HTML 后进入共同正文、避免私有 MD 往返的取舍；不证明所有 Word 无损或库永久最优 |
| 上游 DOCX `var/quality/docx-upstream-source-v1/provenance.json`、`checks-v3.json` | 6 个固定提交与 SHA256 的文件，元数据为 Microsoft Office Word，覆盖 Strict、列表、无表头表格、脚注、文本框和图片关系；属于测试素材，不是用户业务样本 |

## URL 取舍与边界

固定源预期见 `tests/common/url_fixtures.py`。`var/quality/url-phase02/candidate-comparison.json` 对本机 Trafilatura 2.0.0 的 Markdown / HTML 输出作对照：两者均重复部分正文、丢表格跨度和图片候选；Markdown 另丢代码缩进。保留语义文章 DOM，经共同正文及 Writer 输出的同一固定样本通过独立正文/像素/单元格/链接断言。选择基于此结构保真要求，不宣称统计正文提取在所有网页上更差。

唯一 URL 提取规则优先 main/article，清理导航/侧栏并保留语义脚注；无标记页面保留清理后的正文并告警。标记不规范、未声明广告、JS/登录内容不保证提取。保留页面外壳的选择器和资源基准，但将其转换成 block 阅读流，避免原站浮动/flex 导致 PDF 空白；不是原站像素复刻。

真实来源为 Python 官方公开教程 `https://docs.python.org/3/tutorial/introduction.html`；原响应及 7 个资源快照保留在 `real-data/`，`real-fetch.json` 记录获取时间与标识，`real-replay.json` 对源文章 108 个段落/代码/表格块作全数核对，无漏/重及断裂内部链接。12 页 PDF 无空白，前两页和中文固定样本已视觉检查，不代表所有页排版均已验收。离线重放与历史 URL-only 来源共用同一捕获/读取流程，旧原件不改写。

## 样本解释边界

- 真实 EPUB 的 64 个历史段落探针中 37 个精确匹配，另 27 个按源编号/字体证据分类；抽样不能推出整书丢失率。73 次图片引用、70 个不同图像的核对不替代表格结构检查。
- 整书审计检查源单元、导航、图片和正文顺序；HTML/PDF 字符一致不证明布局、代码空白或分页全部正确。样本与代表页报告见上方定位，当前验证边界由 README 概述。
- 旧安装包及旧产物曾早于共享分节修复；通过计数必须关联源码/检查器哈希，不由目录名称或版本号推断等价。

## 真实旧数据副本

证据：`var/quality/storage-migration-copy/report.json`。源库只读盘点为 1 个来源、6 个 schema 1 修订、6 次导出、548 条产物记录，原件与快照哈希有效。

只在副本升级到 SQLite schema 4；从保留原件生成新修订/导出，保存 6 个旧→新关系，977 个历史文件哈希保持，外键检查无错且下载可用。原数据库与原文件未升级或改写；没有验证不存在的 MD-only 迁移。该证据不能替代后续存储改动复验或格式质量验收。

## 环境依据

离线依赖来源见 `var/quality/cached-wheels/provenance.json`；本机缓存安装不证明在线下载或远程 wheel 字节身份。OCR 服务曾连接失败，模拟响应只证明协议；真实识别质量仍需服务与人工参考。当前验证边界见 README，待验收工作见 TODO。
