# 转换取舍与外部证据

本页只保留选择理由、样本来源、人工观察边界和历史报告定位。报告与原件存放在 Git 忽略的 `var/quality/`；统计和执行结果留在报告内。复验方法见 [测试说明](../tests/common/README.md)，当前缺口见 [TODO](../TODO.md)；历史报告不能替代当前验收。

## 实现取舍

下表报告路径均相对 `var/quality/`。

| 依据 | 保留的理由与适用边界 |
| --- | --- |
| 旧实现 `fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4` 的 [基线](../tests/common/baselines/fc5b9a6.json)；`baseline-final/` | 无目录 EPUB、正文重复、引用、表格与图片丢失等观察支持按样本选择规则，不能按代码新旧选定全格式赢家 |
| `docx-before/checks.json` | 结构损失支持保留 Mammoth 语义 HTML 后进入共同正文，避免私有 Markdown 往返；不证明全部 Word 无损 |
| `docx-upstream-source-v1/provenance.json`、`checks-v3.json` | 固定提交与 SHA256 的 OOXML 样本，元数据为 Microsoft Office Word；属于测试素材，不是用户业务样本 |
| `url-phase02/candidate-comparison.json`；[固定网页样本](../tests/common/url_fixtures.py) | 本机 Trafilatura 2.0.0 的样本对照出现正文重复、表格跨度和图片候选丢失，Markdown 另丢代码缩进，支持保留语义文章 DOM；不推及所有网页。源站浮动 / flex 造成 PDF 空白的观察支持改为 block 阅读流，不承诺像素复刻 |

## 独立观察与报告定位

| 证据目录（相对 `var/quality/`） | 不能由场景定义代替的信息 |
| --- | --- |
| `checkpoints/phase-01-2026-10-16-final/` | `checkpoint.json`、`installed-source-hashes.json`、`artifacts.json` 定位验证版本；`real-epub/full-book.json`、`visual-review.json` 保留整书源对照与代表页观察。未精确匹配的历史段落探针含源编号 / 字体差异，不能直接推算丢失率，图片核对也不能替代表格验收 |
| `url-phase02/` | 真实来源为 [Python 官方教程](https://docs.python.org/3/tutorial/introduction.html) 的一次捕获；`real-data/` 保留响应和资源，`real-fetch.json`、`real-replay.json` 定位捕获与重放，`checkpoint.json`、`fixture-export.json` 定位固定样本。人工仅检查真实 PDF 前两页与中文固定样本 |
| `pdf-phase03/` | `checkpoint.json`、`classification.json`、`installed-report.json` 定位页分类与协议证据；`ocr-environment.json` 只记录当时连通性，见下方环境依据 |
| `markdown-import/` | `checkpoint.json`、`visual-review.json` 保留共享 Markdown 导入的安装与代表页观察，不证明 PDF / 图片迁移完成 |
| `resource-cleanup/` | `report.json` 保留被删旧资源的边界和所留字体身份 |

旧安装包及产物曾早于共享分节修复，复验时需核对报告中的源码、检查器和样本身份；不能据目录名认定对应当前实现。

## 真实旧数据副本

`var/quality/storage-migration-copy/report.json` 保留真实源库盘点、schema 1 修订在副本重建、数据库升级到 schema 4 及新旧关联和文件哈希证据。原数据库和原文件未升级或改写，也未验证仅剩 Markdown 的迁移。该报告的价值是记录真实资料来源与副本边界；当前升级行为见 [UC-03-S2](../tests/E5/UC-03-S2.py)。

## 环境依据

离线依赖来源见 `var/quality/cached-wheels/provenance.json`；本机缓存安装不证明在线下载或远程 wheel 字节身份。`var/quality/pdf-phase03/ocr-environment.json` 记录过 OCR 连接失败，不能以旧连通记录解除 TODO 的真实服务阻塞。
