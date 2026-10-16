# 阶段 01：EPUB / HTML / DOCX 共同链路

记录日期：2026-10-15。状态：实现与源码检查已完成，最终安装及产物复验中；尚未关闭阶段。当前状态继续以 [TODO](../../TODO.md) 为准，本记录不代替执行清单。

## 范围与实现取舍

Web、CLI 单/多文件和目录批处理经 `application/convert.py` 与能力表调用 EPUB、HTML、DOCX 的唯一 Reader。正文为 schema 2 的 `BookRevision` / 有序 HTML 片段，共用资源/样式、MD/HTML/PDF Writer、导出计划、JobRuntime、SQLite schema 4 和发布/下载。

DOCX 保留 Mammoth 语义 HTML，再进入共同正文，取消私有 MD 清洗和 MD→HTML/PDF 往返。旧 DOCX 源实现的独立结构样本有 13 项失败，支持保留源结构这一取舍；这不证明所有 Word 文档均无损，也不预定现有库永远胜出。

## 关键改动

- 入口/调度：`src/any_to_md/application/{convert,capabilities,jobs}.py`；`interfaces/cli/convert_book.py`；Web 对已接入格式只转发共同服务。
- DOCX：`readers/docx/{reader,package}.py`。读取 OOXML 部件、Strict 命名空间、符号、编号实例和注释/脚注；仅在内存准备 Mammoth 输入，原件身份不变。处理自定义和根级主部件的资源关系。
- 共同正文：`content/{document,revision,dom,media,styles}.py`。统一锚点、图片、SVG、CSS 和资源预算；`ContentFragment.heading_before=False` 保持章前内容的位置，仅非默认值写入快照。保留空分节边界，避免标题顺序改变；EPUB 默认分片及快照字段保持。
- 共同 Writer：`writers/serialization.py` 保留嵌套/带编号列表、注释锚点、代码软换行和表格行/表头角色；`writers/pdf.py` 启用 HTML 列表属性提示，避免 PDF 忽略起点和 Roman 格式。
- 删旧：`epub_to_md/`、`epub_to_html/`、`html_to_md/`、`html_to_pdf/`、`docx_to_md/` 的业务源码与 wheel 引用，以及 `convert_books.py`；资料目录可能仍存在。未恢复备用管线。

## 已验证与证据边界

- 最新源码全套 **99 项通过，零失败、零跳过**，包括实际 PDF 渲染。Python 复审对本轮修复没有未解决的 CRITICAL/HIGH/MEDIUM；另验证无标题、前置图片/锚点、多分节引言、空首分节、嵌套编号与跨章回链。
- 固定 DOCX 样本覆盖中文、正文顺序、起点/重启/混合编号、合并表格、表内图、代码空行/缩进、重复注释/脚注、Strict、自定义部件、失败持久化、重放、CLI 与 HTTP 四视图。
- 补充 6 个固定上游 DOCX 文件（文件元数据为 Microsoft Office Word）：Strict、列表、2×2 无表头表格、脚注、文本框、图片关系；四视图源标记、表格坐标、注释目标与图像像素检查通过。它们不是用户真实业务样本。来源固定到上游提交和 SHA256。
- 最新已生成的代表产物：DOCX `canonical-docx-final-v3`、HTML `canonical-html-docx-final-v3`、真实 EPUB `canonical-real-v12`。这些产物生成后又修复了 section 内引言和空首分节边界，不能据此宣称最终代码的产物复验已经完成。
- 当时的真实 EPUB：16 个 spine、10042 个源单元、11 项导航、73 次图片引用匹配；348 页 PDF 的 310919 个规范化字符与独立审计 HTML 一致，17 个 HTML/MD 文件与 v9 相同。已看 DOCX 两页、HTML 首页及 EPUB 第 1、14、33、348 页；最终变更后需更新相关产物证据。
- 独立安装此前 **84 项通过、53 个生产模块来源正确**，但该 wheel 早于最新分节修复。核对确认安装中的 `content.dom`、`content.revision` 与当前源码不同，因此安装门槛仍未完成。
- 真实旧库副本的升级与 977 个历史文件哈希保持记录继续有效；本轮没有改写原数据库。共享任务/存储已统一，URL/PDF/图片转换业务仍未统一。

阶段专用检查点：`var/quality/checkpoints/phase-01-2026-10-15-progress-01/checkpoint.json`，含当前源码/检查代码哈希、源码测试日志副本，以及上次安装报告/日志副本与版本不匹配记录。此目录和下列大报告均被 Git 忽略，换机器时不能假定存在；源码和测试保留在仓库，可按命令重建。

- `var/quality/docx-upstream-source-v1/provenance.json`、`checks-v3.json`：上游来源固定信息与对应版本四视图检查。
- `var/quality/docx-before/checks.json`：旧实现缺陷对照。
- `var/quality/canonical-real-v12/full-book.json`：对应版本的整书源审计。

## 重建与验证

本机使用 Python 3.13、项目 `.venv` 和 Homebrew PDF 原生库。其他机器按实际安装位置配置动态库路径。

```bash
ANY_TO_MD_TEST_PDF=1 DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
XDG_CACHE_HOME="$PWD/var/quality/cache" PYTHONPATH=src \
.venv/bin/python -m unittest discover -s tests -t . -v
```

构建并安装 wheel 到源码目录外的独立 venv，再执行：

```bash
.venv/bin/python -m tests.quality.installed \
  --python /absolute/path/to/installed-venv/bin/python \
  --report /absolute/path/to/installed-report.json --pdf
```

本机离线构建/安装命令和完整质量工具说明见 [质量方法](../../tests/quality/README.md)；真实 EPUB 源审计由 `python -m tests.quality.full_book --input <源文件> --export <导出目录> --report <报告路径> --pdf` 执行。缺少原书或真实 OCR 服务时相应效果检查保留未验收，不能拿自建样本替代。

## 限制与转交

DOCX 页眉/页脚的文字、图形和字段尚不保留到重排版正文，发现时明确 `partial`；未知符号、自定义编号等不能完整保留时同样报告。只支持 `.docx`，不支持 `.doc`。HTML/SVG 的 `foreignObject` 尚不能安全跨格式保留。PDF 是重排版视图，不是 Word 原始画布；真实 EPUB 仍有生成标题页等已知排版限制。

Web 浏览器访问曾被拒绝，完整上传→转换→下载 UI 检查未完成，不绕过授权；HTTP 自动检查不能替代该项。OCR 服务此前不可用，真实识别质量未验收。不要顺带删除 `.DS_Store`、原件、用户资料或历史导出。

下一步：先重建最终 wheel、完成源码外安装及当前产物复验，补充版本关联证据并关闭阶段；然后进入 TODO 的 URL 阶段，不重新设计共同基础能力。
