# any_to_md

**最后更新：2026-10-16**

文档转换工具，当前包含 Web 服务、EPUB 书籍转换核心和多组格式转换脚本。

项目目标已重新规划：**同一职责只保留一套正式实现，所有 Web、CLI 和批处理入口调用同一个应用服务，以实际转换质量决定保留和整合的实现。** 不因代码较新或旧文档要求而优先保留某条路线，也不把重复实现包装成长期兼容层。

EPUB、HTML、DOCX 和 URL 已接入同一应用服务，使用规范 HTML 正文和共享 MD/HTML/PDF Writer；旧 EPUB、HTML、DOCX、URL 转换源码及独立批处理源码已删除。Web 全格式及当前 CLI/批处理共用持久任务、存储和发布，PDF、图片仍有私有转换业务。接入不等于效果验收通过，当前实现同样接受样本比较。

新会话从 [项目 TODO](TODO.md) 导航至 [转换收敛 TODO](docs/conversion/TODO.md)，查看当前状态、验证结果和下一步。协作规则见 [AGEMTS.md](AGEMTS.md)，取舍依据见 [转换证据](docs/conversion/evidence.md)，验证操作见 [质量检查](tests/quality/README.md)。

## 规划与契约

- [项目协作约定](AGEMTS.md)：唯一来源、文档职责与任务维护规则。
- [转换架构与实现收敛方案](specs/0002-conversion-architecture.md)：目标架构、实现选择和退出规则。
- [项目导航](TODO.md) / [专题清单](docs/conversion/TODO.md)：文档入口与实施、验收任务。
- [书籍结构与导航契约](specs/0003-book-structure-and-navigation.md)：章节树、整体/分章视图和定位索引的补充约束。
- [SQLite 持久化契约](specs/0004-sqlite-persistence.md)：身份、修订、产物和任务持久化的补充约束。

目标以当前用户要求为准，实际能力以源码及当前版本产物核对，执行状态维护在专题 TODO。设计文档提供补充约束，不决定保留哪套实现；文档中的目标能力不代表当前已经实现。

## 当前代码结构

| 位置 | 当前职责与限制 |
| --- | --- |
| `src/any_to_md/` | EPUB/HTML/DOCX/URL Reader、schema 2 的规范 HTML 片段、共享 MD/HTML/PDF Writer、导出包、SQLite 及共同应用服务 |
| `web/` | FastAPI、上传/下载、任务及格式调度；EPUB/HTML/DOCX/URL 已接入应用服务，PDF/图片仍调用根包；任务、存储、发布和下载已共用核心运行时 |
| 根目录各格式转换包 | PDF、图片等待逐项切换；旧 EPUB、HTML、DOCX 和 URL 业务源码已删除 |
| `scripts/run/convert_book.py` | 书籍 CLI 的薄启动脚本 |
| `any-to-md-book --input-dir` | 批处理复用相同应用服务；旧 `convert_books.py` 已删除 |
| `md_to_any/` | Java 历史实现及资源，待清理；正式 PDF Writer 使用包内字体，不再从此目录加载 |

不同格式的 Reader、不同输出的 Writer 和整体/分章视图可以共存，但同一格式的解析、资源处理、章节识别和导出编排不能由多个入口分别实现。后续以代表性样本、确定性断言和人工渲染检查比较质量，整合必要能力后移除被替代的实现。

## 当前可用入口

以下命令根据当前代码和配置列出。实现接入、源码验证与独立安装验收分别判断，详细状态与限制只在 [专题 TODO](docs/conversion/TODO.md) 维护。

### 环境准备

项目声明 Python 3.13+，使用 `uv` 管理依赖，具体依赖见 [pyproject.toml](pyproject.toml)。

```bash
uv sync
```

### Web 服务

```bash
uv run any-to-md
```

默认访问地址为 <http://localhost:8888>，API 文档位于 <http://localhost:8888/docs>。现有替代启动方式：

```bash
uv run uvicorn web.app:app --reload --host 0.0.0.0 --port 8888
uv run python -m web.app
```

配置由环境变量或 `.env` 提供，见 [web/config.py](web/config.py)：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `PORT` | `8888` | 服务端口 |
| `DATA_DIR` | `var` | Web 与 EPUB CLI 共用的原件、快照、暂存、导出、诊断与 SQLite 根目录 |
| `MAX_UPLOAD_SIZE` | `104857600` | 上传大小上限，100 MiB |
| `OCR_API_URL` | `http://localhost:9122/api/v1/ocr/process` | 现有 OCR 服务地址 |
| `OCR_TIMEOUT` | `600` | OCR 请求超时，秒 |

OCR 服务需另行提供；配置存在不代表 OCR 服务当前可用。PDF 已共用全页分析，损坏/加密不会自动转 OCR；原生与位图共存标作混合，但普通插图不自动触发整本 OCR。OCR 客户端已唯一化并校验 ZIP 路径/配额，实际页级组合和识别质量仍按 TODO 验收。`TEMP_DIR` 和 `FILE_EXPIRE_SECONDS` 已退出配置；原件和发布产物不按一小时 TTL 删除。首次打开旧数据库会自动备份并升级结构，历史快照和文件保持原样；旧数据需要新输出时从保留原件重建，保留旧→新修订关系。

上传返回的 `task_id` 即持久任务 ID。对已结束任务再次转换会返回新 `task_id`，客户端应使用响应 ID 继续查询；原任务和下载保留。下载只使用已发布的产物记录并验证哈希。

### EPUB 书籍 CLI

```bash
uv run any-to-md-book \
  --input "/absolute/path/to/book.epub" \
  --data-dir "./var" \
  --target markdown html pdf
```

同一 CLI 的脚本入口：

```bash
uv run python scripts/run/convert_book.py \
  --input "/absolute/path/to/book.epub" \
  --data-dir "./var" \
  --view both
```

当前代码接受 EPUB、HTML/HTM、HTML 资源 ZIP、DOCX 和保存 URL 文本的 `.url` 文件；当前质量缺口与验收范围见 TODO。`--input` 可传多个文件，或使用 `--input-dir` 读取目录。`--target` 可选 `markdown html pdf`，默认 `markdown`；选择 Markdown 时固定生成整体及分章视图，保留的 `--view` 参数只接受 `both`。

```bash
uv run any-to-md-book --input-dir "./books" --data-dir "./var" --target markdown html pdf
```

网页可直接传一个或多个 URL，或在目录中保存 UTF-8 `.url` 文本参与同一批处理：

```bash
uv run any-to-md-book --url "https://example.com/article" --data-dir "./var" --target markdown html pdf
```

URL 首次转换保存原响应、重定向地址及必要资源快照；同一持久任务再次选择输出时只读快照。仅接受 HTTP/HTTPS，不执行网页 JavaScript。正文优先使用 `main` / `article`，缺少语义标记时报告边界告警；页面外壳按阅读流排版，正文内部结构与资源进入共享规范化。网页标题用于展示，产物使用固定安全名称。

这条路径从一份规范正文和导出计划生成所选输出，发布到新的 `var/exports/<export_id>/` 目录，保留早期导出，并在 `var/catalog.sqlite` 记录身份、修订和导出状态。产物按所选格式包含 `book.md`、`chapters/`、`TOC.md`、`book.html`、`book.pdf`、下载包，以及 `toc.json`、`locations.json`、`book.json`、`report.json`、`manifest.json` 和 `assets/`。PDF 需要可用的 WeasyPrint 原生渲染依赖。

当前规范正文已承载图片、表格跨度、代码、脚注、锚点及作用域样式；关键能力的固定样本通过不代表所有真实文件均无损。检查 `report.json` 的诊断，完整比较与剩余问题见 TODO。

EPUB 和 HTML→PDF 的旧 CLI、旧批处理及转换源码已删除，历史实现仅从 Git 获取用于对照。`epub_to_html/`、`html_to_pdf/` 等名称的目录可能因保留用户资料而存在，不再是正式转换包。其他格式按 TODO 逐项切换并同步删旧。
