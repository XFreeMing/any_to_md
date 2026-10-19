# 转换职责与依赖边界

更新日期：2026-10-19。

本文只保留跨模块约束与设计理由；行为细节由场景代码维护，未完成契约与实施顺序见 [TODO](../TODO.md)，阅读入口见 [README](../README.md)。以下约束不代表所有路径已完成迁移或验收。

## 唯一调用链

```text
Web / CLI / 批处理
        ↓
应用服务：输入路由、任务执行与终态
        ↓
格式 Reader → 共享正文规范化 → BookRevision
                                  ↓
                             ExportPlan
                              ↙       ↘
                     Markdown Writer  HTML Writer → PDF 渲染
                              ↘       ↙
                          共同清单、打包与发布
```

- 同一输入格式只有一个正式 Reader，同一输出只有一个 Writer；入口差异只处理交互与参数，不复制业务规则。对应实现入口为 [能力表](../src/any_to_md/application/capabilities.py)、[转换用例](../src/any_to_md/application/convert.py) 和 [任务运行时](../src/any_to_md/application/jobs.py)。
- Reader 解释来源并返回规范内容，不写最终输出；Writer 只消费规范内容与导出计划，不回读原 EPUB/PDF。PDF 消费 HTML Writer 的打印结果，避免三种输出分别解释正文。
- 正文、来源与资源身份由 [内容契约](0003-book-structure-and-navigation.md) 约束；任务、发布与数据生命周期由 [存储契约](0004-sqlite-persistence.md) 约束。源格式的差异留在 Reader，资源、引用、命名和发布的共性集中复用。
- 能力表同时约束输入校验、分派与可选输出；有 Writer 不等于任意输入组合可用。缺 OCR 或渲染依赖必须说明原因。
- OCR、HTTP、SQLite 和文件系统是 adapter 边界。配置由应用入口注入，核心模块不读取 `web.config`；同步与异步入口调用同一业务实现。
- 正式业务归入 `src/any_to_md/`；`scripts/run/` 只作参数转发。目录按实际职责建立，不为未来能力预建空框架。

## 正文与转换取舍

规范内容使用可序列化 HTML 片段，保留结构与必要样式；Markdown 是派生输出。这样表格跨度、脚注和资源不会因先转 Markdown 再回转而提前丢失。DOCX 的语义 HTML、HTML/网页的源 DOM 与 EPUB 的章节内容都进入这一条路径，取舍样本见 [转换依据](../docs/conversion/evidence.md#实现取舍)。

上游 PDF/OCR 只有 Markdown 时，由 [共享 Markdown 导入](../src/any_to_md/content/markdown.py) 转成 DOM 后规范化；这不是独立的 `.md` 用户输入承诺。上游已丢失的信息不能恢复，必须保留原响应与损失说明。HTML 骨架、Markdown 序列化和 PDF 打印规则分别由唯一 Writer 维护，不回流到各 Reader。

不追求原版面像素复刻；保留内容、结构与阅读顺序优先。没有可靠章节的来源不虚构分章，无法表达的内容必须有诊断，不能把“生成了文件”当作完整转换。

## PDF / 图片边界与切换依赖

[PDF 分析](../src/any_to_md/readers/pdf/analysis.py) 提供页级事实，不能代替正式 Reader 的提取与组装。原生文字与 OCR 是互补处理：每页需要来源定位、覆盖状态及失败原因，按原页序组装并消除重叠。`mixed` 只说明文字与位图共存；普通插图不等于需要 OCR，损坏或加密也不等于扫描件。

OCR 网络协议只由 [OCR adapter](../src/any_to_md/adapters/ocr.py) 承担。服务返回成功不能证明识别质量或页覆盖；页级策略需有真实服务和人工参考依据。无可用正文则失败，主体可用但丢失内容则明确标为部分结果；完整的输入、输出与异常 Examples 在 [TODO](../TODO.md) 和对应场景内维护。

当前 [PDF 入口](../pdf_to_md/converter.py) 仍调用 [图片入口](../ocr_to_md/converter.py)，两者构成一个切换单元：替代 Reader、共同发布和全部入口验收后一起切换并删除旧业务包，不能提前删除被 PDF 使用的图片包。迁移不保留双引擎、运行时 fallback 或永久旧包装；尚未替换的格式保持现有唯一入口，执行任务只列在 TODO。

切换与删除源码必须保留用户原件、数据库、修订和历史输出。旧实现仅可通过隔离的 Git 版本作对照；发布回退处理整个版本，数据回退按备份契约独立处理。

## 行为入口

PDF / 图片行为见 [E1 场景](../tests/E1/)，安装、发布与浏览器行为见 [E2 场景](../tests/E2/)。文件存在不代表场景通过；当前未验收范围留在 [TODO](../TODO.md)，执行与证据判定见 [测试说明](../tests/common/README.md)。本文不复制各场景的步骤、Examples 或通过记录。
