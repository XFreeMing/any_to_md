# any_to_md

**当前概况：** any_to_md 通过 Web、CLI 和目录批处理将 EPUB、HTML、DOCX 与网页 URL 转换为 Markdown、HTML、PDF，提供持久任务、原件保存和产物校验，以用例场景测试判定开发完成，PDF 与图片目前通过 Web 转换，其共同 Reader、页级 OCR 组合及完整交付验收仍见 [TODO.md](TODO.md)。

## 已具备的功能

| 输入 | Web 输出 | CLI / 目录批处理 | 实现范围 |
| --- | --- | --- | --- |
| EPUB | 整体 / 分章 Markdown、HTML、PDF | Markdown、HTML、PDF | 共同 Reader、章节树、正文与 Writer |
| HTML / HTM、HTML 资源 ZIP | 整体 / 分章 Markdown、HTML、PDF | Markdown、HTML、PDF | 共同正文、资源与引用处理 |
| DOCX | 整体 / 分章 Markdown、HTML、PDF | Markdown、HTML、PDF | 语义 HTML 导入共同正文；不支持 `.doc` |
| HTTP/HTTPS URL、UTF-8 `.url` 文件 | 整体 Markdown、HTML、PDF | Markdown、HTML、PDF | 保存响应与资源快照后转换，支持离线重放 |
| PDF | 整体 Markdown、HTML、重排版 PDF | 尚未接入 | 共用逐页分析，转换仍走根目录 PDF 包；扫描件依赖外部 OCR |
| PNG / JPG / JPEG / GIF / BMP / WebP | 整体 Markdown、HTML、PDF | 尚未接入 | 根目录图片入口调用共享 OCR provider，依赖外部 OCR |

CLI 选择 Markdown 时同时生成整体与分章视图；Web 的下载选项按输入格式区分。上表表示代码提供的能力，真实识别效果与完整交付范围仍需按样本验收；[剩余用户故事](TODO.md) 是唯一待办入口。

- **正文与导出**：EPUB、HTML、DOCX、URL 共用规范 HTML 正文，承载章节、图片、表格跨度、代码、脚注、锚点和作用域样式，由共享 Writer 生成所选输出、导航、资源包及损失诊断。
- **任务与存储**：Web 全格式和当前 CLI 共用 SQLite 持久任务、原件保存、存储与发布；不同导出使用独立目录，历史修订和产物保留，下载按发布记录校验大小与哈希。
- **网页快照**：优先提取 `main` / `article`，保存原响应、重定向地址与必要资源；同一已捕获来源选择其他输出时读取快照，不再次抓取正文。
- **PDF / OCR 基础能力**：全页分析区分原生、扫描、混合和空白页，损坏或加密文件明确失败；共享 OCR 客户端提供响应、资源和诊断，并校验 ZIP 路径与配额，内部 Markdown 导入复用共同正文和渲染组件。

## 当前代码结构

```text
any_to_md/
├── AGENTS.md                 # 协作与文档维护规则
├── README.md                 # 整体现状、能力、结构与使用方式
├── TODO.md                   # 唯一未完成用户故事清单
├── pyproject.toml / uv.lock  # 依赖、命令与打包配置
├── src/any_to_md/
│   ├── application/         # 能力表、转换用例与持久任务
│   ├── domain/              # 正文、任务和产物模型
│   ├── readers/             # EPUB / HTML / DOCX / URL Reader、PDF 分析
│   ├── content/             # 规范正文、样式、资源、引用及内部 MD 导入
│   ├── writers/             # Markdown / HTML / PDF 输出
│   ├── export/              # 视图计划、命名、导航、清单与下载包
│   ├── adapters/            # SQLite、存储、HTTP、网页快照、OCR、归档
│   ├── interfaces/cli/      # 单文件、多文件、URL 与目录批处理入口
│   └── resources/fonts/     # 正式渲染字体
├── web/                     # FastAPI、静态界面、上传/转换/下载接口
├── pdf_to_md/               # 当前仍在使用的 PDF 转换入口
├── ocr_to_md/               # 当前仍在使用的图片转换入口
├── scripts/run/             # CLI 薄启动脚本
├── tests/
│   ├── common/             # 通用样本、检查器、执行器、安装与历史对照工具
│   ├── E1/                 # PDF / 图片：UC-01、UC-02 场景
│   └── E2/                 # 安装 / 浏览器：UC-03、UC-04 场景
├── specs/                   # 转换、正文导航与存储契约
├── docs/conversion/         # 实现取舍与验证依据
├── input/、output/          # 本地素材与输出
└── var/                     # 本地运行数据和验证产物（Git 忽略）
```

`web/` 尚在根目录，`readers/pdf/` 只有分析模块，图片尚无共同 Reader；目标结构见 [转换架构](specs/0002-conversion-architecture.md)。本地残留的 `epub_to_html/`、`html_to_pdf/` 等目录可能保留用户资料或产物，已不包含正式转换源码，不能据目录名称推断当前功能。

## 使用方式

### 环境与 Web

项目要求 Python 3.13+，使用 `uv` 管理依赖：

```bash
uv sync
uv run any-to-md
```

启动后访问 [Web 界面](http://localhost:8888)，接口说明见 [API 文档](http://localhost:8888/docs)。开发时也可运行：

```bash
uv run uvicorn web.app:app --reload --host 0.0.0.0 --port 8888
```

PDF 输出需要可用的 WeasyPrint 原生依赖；扫描 PDF 和图片识别需要另行提供 OCR 服务。环境与验证命令见 [质量检查](tests/common/README.md)。

Web 配置来自环境变量或 `.env`，定义见 [web/config.py](web/config.py)：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8888` | 服务端口 |
| `DATA_DIR` | `var` | Web 持久数据根目录 |
| `MAX_UPLOAD_SIZE` | `104857600` | 上传大小上限，100 MiB |
| `OCR_API_URL` | `http://localhost:9122/api/v1/ocr/process` | 外部 OCR 服务地址 |
| `OCR_TIMEOUT` | `600` | OCR 请求超时，秒 |

上传返回的 `task_id` 是持久任务 ID；对已结束任务重新转换会返回新 ID，客户端需使用响应 ID 查询，原任务和历史下载仍保留。

### CLI 与批处理

单文件转换，可在 `--input` 后传多个文件：

```bash
uv run any-to-md-book \
  --input "/absolute/path/to/book.epub" \
  --data-dir "./var" \
  --target markdown html pdf
```

目录批处理与网页转换：

```bash
uv run any-to-md-book --input-dir "./books" --data-dir "./var" --target markdown html pdf
uv run any-to-md-book --url "https://example.com/article" --data-dir "./var" --target markdown html pdf
```

`--input-dir` 只读取目录直属文件，不递归；接受 EPUB、HTML/HTM、HTML 资源 ZIP、DOCX 和保存 URL 的 UTF-8 `.url` 文件。`--url` 可传多个 URL；`--input`、`--input-dir`、`--url` 三选一。PDF 和图片尚不能通过此 CLI 转换。

`--target` 可选 `markdown html pdf`，默认 `markdown`；Markdown 固定生成整体与分章视图，`--view` 只接受 `both`。模块入口 `uv run python -m any_to_md.interfaces.cli.convert_book` 和脚本入口 `uv run python scripts/run/convert_book.py` 接受相同参数。转换退出码 `0` 表示全部成功，`2` 表示有部分保留结果但无失败，`1` 表示发生失败；参数解析错误也会退出 `2`，应结合 stderr、逐项状态和报告区分。

CLI 使用 `--data-dir`（默认 `./var`），需要与 Web 共享数据时显式指向相同目录，不依赖 Web 的 `DATA_DIR` 配置。

### 数据与产物

数据根目录包含 `catalog.sqlite`、原件 `sources/`、正文修订 `revisions/`、任务 `jobs/`、暂存 `staging/` 和发布结果 `exports/<export_id>/`。原件、修订和发布产物不按临时文件的一小时 TTL 删除；旧数据库打开时会先备份再升级结构，历史文件保持原样，需要新版输出时从保留原件重建并记录旧→新修订关系。

共同链路按所选格式生成 `book.md`、`chapters/`、`TOC.md`、`book.html`、`book.pdf` 和下载包，并提供 `toc.json`、`locations.json`、`book.json`、`report.json`、`manifest.json` 与 `assets/`；具体产物以对应导出清单为准，PDF/图片现有路径不能据此视为已具备共同修订。使用结果前查看 `report.json` 的诊断和损失说明。

## 能力与验证边界

测试按所属 Epic 组织，`tests/` 一级只有 `common/` 和 Epic 目录，通用辅助统一放在 `common/`；例如 `tests/E1/UC-01-S1.py` 对应 TODO 的 `UC-01-S1`，编号保持一致。相关场景全部通过才算开发完成，失败或阻塞继续留在 TODO。完成故事的场景仍保留回归，执行方式与环境要求见 [场景测试方法](tests/common/README.md)。

- DOCX 页眉页脚、未知符号和自定义编号等未完整保留时报告 `partial`；固定样本通过不代表所有业务文件无损。
- HTML/SVG `foreignObject` 尚不能安全跨格式保留；PDF 输出采用重排版，EPUB 标题和封面有已知排版限制，代表页检查不等于逐页布局验收。
- URL 只接受 HTTP/HTTPS，不执行 JavaScript、不穿过登录；无语义文章标记时清理正文并告警，不保证识别全部广告，必要响应支持 identity/gzip，超配额会失败。
- PDF 的逐页分析不等于逐页组合：当前仍按整份文档选择原生提取或 OCR；混合分类只表示原生内容与位图共存，不保证位图文字覆盖，普通插图不会自动触发整本 OCR。
- 内部 Markdown 导入用于承接上游结果，不是独立 `.md` 上传或 CLI 输入能力；上游已丢失的信息不能经导入恢复。
- 既有技术证据覆盖固定样本、独立安装、部分真实文档与代表页；模拟 OCR 只证明协议，旧数据副本验证不证明原库已升级或仅剩 Markdown 的数据迁移，不能据此推断真实 OCR、人工试用或部署已验收。

未完成事项、依赖和验收标准统一维护在 [TODO.md](TODO.md)，样本、报告与适用范围见 [转换依据](docs/conversion/evidence.md)。

## 设计与维护入口

- [AGENTS.md](AGENTS.md)：唯一来源、用户故事与文档更新约定。
- [转换架构](specs/0002-conversion-architecture.md)：职责、调用链与实现取舍。
- [内容与导航契约](specs/0003-book-structure-and-navigation.md)：正文、章节和多视图。
- [存储契约](specs/0004-sqlite-persistence.md)：任务、修订、发布和数据保留。
- [质量检查](tests/common/README.md)：源码、独立安装和真实样本复验。
