# 质量检查方法

这里保存独立源样本、判定条件与检查工具，不包含第二套生产转换器。当前进度、通过范围和下一步只见 [TODO](../../TODO.md)；阶段结果及历史对照见 [归档索引](../../docs/archive/README.md)，执行与归档规则见 [agent.md](../../agent.md)。本文件不维护实时通过计数。

## 独立源预期与检查器

- `fixtures.py` / `cases.json` 固定原始正文、顺序、图像像素、表格坐标/跨度、代码和链接目标；`docx_fixtures.py` 另构造 OOXML 列表、评论、脚注及代码等结构，不使用生产 Reader 生成预期。
- `inspection.py` 对整体 MD、分章 MD、HTML 分别检查，不将多种视图合计成重复正文。图片按解码后的尺寸/RGB 像素核对，不能用重命名或损坏文件代替。
- MD 检查经 Python Markdown 的 `tables`、`fenced_code`、`toc` 和 Unicode 标题 slug 渲染；显式锚点应保持可定位。源行列/单元格角色、标题相对正文位置及列表语义另有端到端断言。
- PDF 的通用检查核对正文标记、次序及图像；格式测试另核对实际编号与标题位置。布局、可读性、分页和视觉效果仍须渲染代表页观察，不能由文本相同推断全部排版一致。
- 报告的 `passed` 只对应其 `automatic_scope`；视觉/OCR 状态独立记录，不把文件生成成功当作完整验收。检查器自身有删除、重复、乱序、断链、错指、图片和表格损坏等正反例。

## 原生测试

`test_canonical_epub.py`、`test_html_conversion.py`、`test_docx_conversion.py` 覆盖各唯一 Reader 与共享视图；`test_epub_entries.py` 及格式测试中的实际 CLI/HTTP 流程覆盖上传、状态、下载、单/多文件和目录批处理，以及搬移和来源重放。

`test_jobs.py`、`test_publication.py`、`test_catalog_migration.py` 检查持久任务、执行权、重启、并发、发布失败、历史导出保持、校验和和旧 schema 副本重建。它们不代替格式解析效果检查。

```bash
ANY_TO_MD_TEST_PDF=1 \
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
XDG_CACHE_HOME="$PWD/var/quality/cache" \
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t . -v
```

本机使用 Python 3.13 与 Homebrew PDF 原生库，其他环境按实际库路径配置。未显式启用原生 PDF 环境时，相应测试会明确跳过；不能将这种运行写成零跳过的完整验收。

## 独立安装

`installed.py` 在源码目录外启动独立 venv 的 `python -I`，只加载独立检查代码；生产模块与 CLI 子进程须来自安装目录，并检查旧包缺席、字体与注册入口。应重新构建最终代码，另核对安装文件与当前源码哈希，避免仅检查来源路径却用到旧 wheel。

本机离线方式需要已准备的依赖 wheel、构建环境与缓存；它不证明远程依赖下载或远程归档字节身份。其他环境可正常构建并安装到新的虚拟环境。

```bash
uv build --python var/quality/build-environment/bin/python --wheel \
  --no-build-isolation --offline --cache-dir var/quality/install-cache \
  --out-dir var/quality/wheel
uv pip install --python var/quality/installed-epub/bin/python \
  --no-index --find-links var/quality/cached-wheels \
  --cache-dir var/quality/install-cache --reinstall-package any-to-md \
  var/quality/wheel/any_to_md-0.1.0-py3-none-any.whl
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
XDG_CACHE_HOME="$PWD/var/quality/cache" \
.venv/bin/python -m tests.quality.installed \
  --python var/quality/installed-epub/bin/python \
  --report var/quality/installed-report.json --pdf
```

## 真实 EPUB 源对照

```bash
.venv/bin/python -m tests.quality.full_book \
  --input "/absolute/path/to/book.epub" \
  --export "/absolute/path/to/published-export" \
  --report var/quality/private-full-book.json --pdf
```

`full_book.py` 直接读取源 ZIP/XML 的 OPF、spine、NCX 或 EPUB3 nav，不导入生产 Reader/Writer。检查所有文本单元的次数与顺序、源图片字节与顺序、导航/片段归属、元数据、目录和分章导航；PDF 与独立审计后的 HTML 全文比较。

只允许 Unicode NFKC/空白规范化，以及有明确源字体和连续编号证据的印刷行号处理；未知、冲突、条件、导入或嵌套字体不批准删号。`test_full_book.py` 验证这些边界及缺段/重复/乱序、伪造节点和导航等反例。整书文本比较不能证明每页布局或代码空白完全正确。

## 隔离历史对照

```bash
QUALITY_SOURCE_TREE="$(mktemp -d)"
git archive fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4 | tar -x -C "$QUALITY_SOURCE_TREE"
.venv/bin/python -m tests.quality.capture \
  --source-tree "$QUALITY_SOURCE_TREE" \
  --revision fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4 \
  --output var/quality/new-baseline --render-pdf
```

输出目录必须是新的。捕获器拒绝归档外导入、已加载第一方模块和运行中修改代码/检查器，记录来源及哈希；退出 0 只代表报告写出。该历史工具只适用于确实含所需旧包的归档，不能为运行它而恢复生产旧引擎。

历史基线、抽样与真实旧库副本记录已移入 [历史质量归档](../../docs/archive/2026-10-15-quality-baseline-and-storage.md)。用户书籍、完整输出和大报告保持在忽略的 `var/quality/`；跨机器重建需要相应原件和运行环境。阶段关闭前按 agent.md 保存专用证据副本，避免后续覆盖 `current-*` 日志导致归档失真。
