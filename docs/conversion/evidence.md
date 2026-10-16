# 转换取舍与验证依据

更新日期：2026-10-16。

本文维护实现取舍和样本适用范围；当前状态见 [专题 TODO](TODO.md)，操作见 [质量检查](../../tests/quality/README.md)，机制见 [转换架构](../../specs/0002-conversion-architecture.md)。本地报告位于不随 Git 分发的 `var/quality/`，缺失时需按质量检查说明准备原件和环境重建。

## 实现取舍

| 依据 | 支持的结论与限制 |
| --- | --- |
| 旧实现基线 `fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4`；[公开报告](../../tests/quality/baselines/fc5b9a6.json)，完整报告 `var/quality/baseline-final/` | 58 个组合中 43 个完成转换、6 个异常、9 个 OCR 组合未运行。复现无目录 EPUB 异常、章界重复、导航断链、表格跨度/图片丢失、HTML 下载路径、URL 来源路径及混合 PDF 检测问题；没有全格式赢家证据 |
| DOCX 独立结构对照 `var/quality/docx-before/checks.json` | 旧实现有 13 项失败，支持保留 Mammoth 语义 HTML 后进入共同正文、避免私有 MD 往返的取舍；不证明所有 Word 无损或库永久最优 |
| 上游 DOCX `var/quality/docx-upstream-source-v1/provenance.json`、`checks-v3.json` | 6 个固定提交与 SHA256 的文件，元数据为 Microsoft Office Word，覆盖 Strict、列表、无表头表格、脚注、文本框和图片关系；属于测试素材，不是用户业务样本 |

## 样本解释边界

- 真实 EPUB 的 64 个历史段落探针中 37 个精确匹配，另 27 个按源编号/字体证据分类；抽样不能推出整书丢失率。73 次图片引用、70 个不同图像的核对不替代表格结构检查。
- 整书审计检查源单元、导航、图片和正文顺序；HTML/PDF 字符一致不证明布局、代码空白或分页全部正确。当前样本与代表页结果只在专题 TODO 引用。
- 旧安装包及旧产物曾早于共享分节修复；通过计数必须关联源码/检查器哈希，不由目录名称或版本号推断等价。

## 真实旧数据副本

证据：`var/quality/storage-migration-copy/report.json`。源库只读盘点为 1 个来源、6 个 schema 1 修订、6 次导出、548 条产物记录，原件与快照哈希有效。

只在副本升级到 SQLite schema 4；从保留原件生成新修订/导出，保存 6 个旧→新关系，977 个历史文件哈希保持，外键检查无错且下载可用。原数据库与原文件未升级或改写；没有验证不存在的 MD-only 迁移。该证据不能替代后续存储改动复验或格式质量验收。

## 环境依据

离线依赖来源见 `var/quality/cached-wheels/provenance.json`；本机缓存安装不证明在线下载或远程 wheel 字节身份。OCR 服务曾连接失败，模拟响应只证明协议；真实识别质量仍需服务与人工参考。当前访问限制及未验收项见专题 TODO。
