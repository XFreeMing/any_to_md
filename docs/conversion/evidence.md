# 转换取舍与外部证据

更新日期：2026-10-19。

本文只保存验收代码不能代替的选择理由、样本来源、人工观察和历史报告定位。行为细节看场景代码，统一入口见 [README](../../README.md)；未验收事项见 [TODO](../../TODO.md)，执行与证据格式见 [测试说明](../../tests/common/README.md)。

## 报告定位与适用范围

本地报告在不随 Git 分发的 `var/quality/`；缺失时需按测试说明准备原件与环境复验，不从本页推断通过。报告中的源码、检查器、样本与产物标识只支持对应版本及范围，不能自动继承为当前验收。环境、命令和时间留在报告内，不在本文追加运行日志。

| 证据目录（相对 `var/quality/`） | 保留原因与限制 |
| --- | --- |
| `checkpoints/phase-01-2026-10-16-final/` | `checkpoint.json`、`installed-source-hashes.json`、`artifacts.json` 定位 EPUB / HTML / DOCX 的验证对象；`real-epub/full-book.json`、`visual-review.json` 保留整书源对照与代表页观察，不代表所有文件或逐页布局 |
| `url-phase02/` | `candidate-comparison.json` 记录提取方案取舍；`checkpoint.json`、`fixture-export.json`、`real-fetch.json`、`real-replay.json` 定位固定样本、真实捕获及离线重放 |
| `pdf-phase03/` | `checkpoint.json`、`classification.json`、`installed-report.json` 记录逐页分类与协议检查；`ocr-environment.json` 仅证明当时的连通性检查，不能证明识别质量 |
| `markdown-import/` | `checkpoint.json`、`visual-review.json` 保留独立 Markdown 经共同正文 / Writer 的安装与代表页证据，不证明真实 OCR 或 PDF / 图片迁移完成 |
| `resource-cleanup/` | `report.json` 保存旧资源删除边界与保留字体哈希，不替代当前打包检查 |

## 实现取舍

| 依据 | 决策理由及边界 |
| --- | --- |
| 旧实现 `fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4` 的 [基线报告](../../tests/common/baselines/fc5b9a6.json)，完整报告 `var/quality/baseline-final/` | 58 个组合中 43 个完成转换、6 个异常、9 个 OCR 组合未运行；暴露无目录 EPUB、重复正文、引用、表格与图片丢失等问题，支持按样本承接规则，不能按代码新旧选定全格式赢家 |
| DOCX `var/quality/docx-before/checks.json` | 旧实现 13 项失败支持保留 Mammoth 语义 HTML 后进入共同正文，避免私有 Markdown 往返；不证明所有 Word 无损或库永久最优 |
| DOCX `var/quality/docx-upstream-source-v1/provenance.json`、`checks-v3.json` | 6 个固定提交与 SHA256 的文件覆盖 Strict、列表、表格、脚注、文本框和图片关系；元数据为 Microsoft Office Word，属于测试素材，不是用户业务样本 |

## URL 取舍与边界

固定比较样本保留在 [url_fixtures.py](../../tests/common/url_fixtures.py)，仅用于追溯既有证据，不代表已接入当前场景。`url-phase02/candidate-comparison.json` 对本机 Trafilatura 2.0.0 的 Markdown / HTML 输出作对照：该样本有正文重复、表格跨度和图片候选丢失，Markdown 另丢代码缩进。保留语义文章 DOM 再走共同 Writer 的选择基于这些结构保真要求，不意味着统计提取对所有网页更差。页面外壳转换为 block 阅读流源于原站浮动 / flex 导致 PDF 空白的观察，不承诺像素复刻。

真实来源为 [Python 官方教程](https://docs.python.org/3/tutorial/introduction.html) 的一次捕获：原响应与 7 个资源保留在 `url-phase02/real-data/`，`real-fetch.json` 定位身份，`real-replay.json` 核对 108 个正文块的漏重与链接。12 页 PDF 无空白，只有前两页和中文固定样本做过视觉检查；不代表全部页布局已验收。此处记录捕获证据，不维护产品提取规则副本。

## 样本解释边界

- 真实 EPUB 的 64 个历史段落探针中 37 个精确匹配，另 27 个按源编号 / 字体证据分类，不能据此推算整书丢失率；73 次图片引用、70 个不同图像的核对不替代表格检查。
- 整书审计与 HTML / PDF 文本一致不能证明布局、代码空白和分页正确；人工观察只覆盖报告标明的代表页。
- 旧安装包及产物曾早于共享分节修复，必须核对源码 / 检查器哈希，不能凭目录名、版本号或旧通过计数宣称等价。

## 真实旧数据副本

`var/quality/storage-migration-copy/report.json` 记录源库只读盘点：1 个来源、6 个 schema 1 修订、6 次导出、548 条产物记录，原件与快照哈希有效。仅在副本升级到 SQLite schema 4，并从保留原件生成新修订及 6 个旧→新关系；977 个历史文件哈希保持，外键与下载检查通过。

原数据库和原文件未升级或改写，也未验证不存在的 MD-only 迁移。该报告保留真实资料与副本边界，不能替代当前 [UC-03-S2](../../tests/E2/UC-03-S2.py) 的执行或格式质量验收。

## 环境依据

离线依赖来源见 `var/quality/cached-wheels/provenance.json`；本机缓存安装不证明在线下载或远程 wheel 字节身份。OCR 曾连接失败，模拟响应只证明协议；真实识别须使用可用服务及人工参考，并由相应场景关联证据，不能以历史连通记录解除当前阻塞。
