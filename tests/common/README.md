# 场景测试与质量检查

最后更新：2026-10-19。

本项目以完整用户场景验证开发结果，测试约定见 [AGENTS.md](../../AGENTS.md)，未完成故事与 Gherkin 场景见唯一的 [TODO.md](../../TODO.md)，当前能力与边界见 [项目 README](../../README.md)。本文件维护运行方法；样本、取舍和证据范围见 [转换依据](../../docs/conversion/evidence.md)，不在这里维护待办或历史通过计数。

## 场景与文件对应

[tests/](../) 一级只有 `common/` 与 Epic 目录。场景位于所属 Epic 中，按 `UC-01-S1.py` 命名，与 TODO 中的场景编号一致；一个文件验证一个完整场景，`Scenario Outline` 的所有 Examples 在同一文件内执行。

| 位置 | 内容 |
| --- | --- |
| [E1/](../E1/) | UC-01 / UC-02：PDF 与图片转换场景 |
| [E2/](../E2/) | UC-03 / UC-04：独立安装、数据保留与浏览器场景 |
| `E3/`（实施时建立） | UC-05～UC-08：CLI、文档语义、网页捕获与版本复用场景 |
| [common/](./) | 通用样本、流程驱动、产物检查器、执行器、安装及历史对照工具和测试说明 |

场景覆盖用户入口、转换、状态与实际产物；所有公共辅助放在 `common/`，只提供样本、操作和观察方法，不另建函数级单元测试。

执行器按 TODO 的 Epic 归属发现场景，同时收集所有 Epic 中保留的回归场景；重复编号、错误目录归属或不符合目录规则均不能通过清单检查。计划中的场景可以先只有规格，但必须标明测试待实现；`--list` 显示缺文件，执行该场景会失败，不能以空文件或占位通过消除缺口。进入实施时先建立同名失败测试，再实现行为、使场景通过，最后重构。故事完成后从 TODO 删除，测试文件保留为回归场景，编号不重用；测试粒度保持在用户场景。

只有相关场景、Examples 和规定的输入／入口／输出组合全部通过，才能证明对应开发完成。`failed`、`blocked`、缺文件、空验证结果和未执行范围都不算通过；单个格式或选定场景通过不能代表整个用例通过。需要真实 OCR、人工阅读或部署证据的验收，不能用模拟响应、产物存在或本机运行替代。

## 环境与运行

在仓库根目录使用 Python 3.13+：

```bash
uv sync --group test
.venv/bin/python tests/common/run.py --list
.venv/bin/python tests/common/run.py --report var/quality/scenarios.json
.venv/bin/python tests/common/run.py UC-03-S3 UC-03-S4 \
  --report var/quality/selected-scenarios.json
.venv/bin/python tests/E1/UC-02-S2.py \
  --report var/quality/single-scenario.json
```

单个场景文件也可直接执行，仍通过同一个公共执行器完成断言、证据检查和报告；测试工具使用上述脚本入口，不依赖环境中同名 `tests` 包的模块查找结果。

直接运行公共执行器时，不指定编号会执行全部当前场景和保留的回归场景；指定编号或直接运行单个场景文件时，报告的 `scope` 为 `selected`。`--list` 只检查清单，不运行行为。执行器以退出码 `0` 表示所选范围全部通过，任意失败或阻塞返回 `1`，不允许使用 Python `-O` / `-OO` 关闭断言。

使用 `--report` 时，JSON 记录本次范围、执行时间和逐场景结果，样本、日志及产物保留在报告所在目录的 `artifacts/<运行标识>/<场景编号>/`；不传该参数时使用临时目录。正式复核应保留报告与产物，不能只引用终端通过计数。

PDF 输出需要 WeasyPrint 原生依赖；例如本机 macOS 的 Homebrew 库可这样配置，其他环境使用各自的库路径：

```bash
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
export XDG_CACHE_HOME="$PWD/var/quality/cache"
```

HTTP 和浏览器场景需要允许监听回环端口。浏览器场景使用 Playwright 启动真实 Chrome，并保存截图、`trace.zip` 和 Web 日志。默认使用已安装的 Chrome；如改用 Playwright Chromium：

```bash
.venv/bin/python -m playwright install chromium
export ANY_TO_MD_BROWSER_CHANNEL=chromium
```

缺浏览器、原生库、真实服务或所需资料时，应按报告补足条件并复验，不能通过跳过相关场景获得完整通过结论。

| 环境变量 | 使用范围与要求 |
| --- | --- |
| `ANY_TO_MD_SCENARIO_OCR_URL` | 需要真实识别的场景；填写已确认可用的 OCR 接口地址。模拟服务仅用于明确的异常或协议场景。 |
| `ANY_TO_MD_BROWSER_CHANNEL` | 浏览器通道，默认 `chrome`；使用已安装的 Playwright Chromium 时设为 `chromium`。 |
| `ANY_TO_MD_INSTALLED_PYTHON` | UC-03-S1 使用的独立虚拟环境解释器绝对路径；不能是源码的 editable 安装。 |
| `ANY_TO_MD_INSTALLED_WHEEL` | UC-03-S1 核对的最终源码构建 wheel 的绝对路径。 |
| `ANY_TO_MD_LEGACY_DATA_DIR` | UC-03-S2 使用的真实旧数据一致副本，包含旧数据库、原件、修订和历史导出；来源保持不变，场景另复制后执行升级。 |
| `ANY_TO_MD_VISUAL_EVIDENCE` | 运行时匹配既有人工复核 JSON；对保留报告补充复核使用下文的 `--review-evidence`。 |
| `ANY_TO_MD_FIXTURE_PORT` | 安装与浏览器矩阵的本地 URL 样本端口，默认 `18763`；需保持可用，复用既有证据时保持一致。 |

旧数据副本必须来自已停止写入的环境；存在 WAL/SHM、已经升级为当前结构，或缺少可从原件重建的旧修订时，不能证明旧结构升级。合成样本不能代替这一真实升级场景。

## 人工阅读证据

需要人工判定的场景在技术流程到达阅读检查后生成 `UC-编号-S编号-visual-requirements.json`，位于保留产物目录中；缺少匹配复核时仍为 `blocked`。先查看本次源样本和实际阅读产物，再填写结论，不能根据自动检查结果预填人工通过。

复制生成文件中的 `scenario`、`sources`、`exports` 原值，在同一 JSON 对象中增加以下字段；`checks` 必须逐项覆盖生成文件的 `required_checks`，名称完全一致：

```json
{
  "reviewer": "实际复核者",
  "reviewed_at": "实际复核日期与时间",
  "conclusion": "针对这些样本与产物的具体阅读结论",
  "checks": [
    {
      "name": "从 required_checks 复制的一项检查名称",
      "accepted": false,
      "notes": "实际观察到的内容与问题"
    }
  ]
}
```

上例只是需要补充的字段，不能单独作为证据文件。实际检查符合要求后才将对应 `accepted` 设为 `true`；不能删除不通过项。多个场景的复核对象可放在同一文件的 `{"reviews": [...]}` 数组中。复核文件存放在保留的场景产物目录之外，避免改变已登记的产物身份。补充复核时，使用之前保留的同一份报告：

```bash
.venv/bin/python tests/common/run.py \
  --report var/quality/scenarios.json \
  --review-evidence "/absolute/path/to/visual-review.json"
```

此操作核对保留样本、产物身份及源码未变化，补充已有技术执行的人工结论，不重新转换文件。只有技术路径全部通过、唯一阻塞是人工阅读的场景才能完成；缺 OCR 或其他条件的场景仍未通过。源码或保留产物改变后必须重新运行场景并复核，不能用人工结论覆盖技术失败。

匹配依据是源文件名与 SHA-256，以及实际阅读文件的身份；PDF 按页面渲染像素核对，其他阅读文件和资源按字节核对，排除 `manifest.json`、`report.json` 等非阅读文件。产物应连同相关资源一起保留，不能仅凭截图或文字提取判断整个文档无损。若直接重跑场景，可用 `ANY_TO_MD_VISUAL_EVIDENCE` 指定既有复核；只有当前样本和产物身份仍匹配时才可复用，动态网页抓取等情况应复核保留的原运行产物。

## 独立安装场景

UC-03-S1 在源码目录外使用独立环境执行能力矩阵，同时核对最终源码、wheel 和安装文件的身份。先从当前源码构建，并安装到新的虚拟环境；以下 wheel 文件名须与本次构建输出一致：

```bash
uv build --wheel --out-dir var/quality/wheel
uv venv --python 3.13 var/quality/installed
uv export --locked --only-group test --no-hashes \
  --format requirements.txt --output-file var/quality/test-requirements.txt
uv pip install --python var/quality/installed/bin/python \
  var/quality/wheel/any_to_md-0.1.0-py3-none-any.whl \
  -r var/quality/test-requirements.txt
export ANY_TO_MD_INSTALLED_PYTHON="$PWD/var/quality/installed/bin/python"
export ANY_TO_MD_INSTALLED_WHEEL="$PWD/var/quality/wheel/any_to_md-0.1.0-py3-none-any.whl"
.venv/bin/python tests/common/run.py UC-03-S1 \
  --report var/quality/installed-matrix-scenario.json
```

独立环境仍需原生库、浏览器、真实 OCR 和人工阅读证据。离线构建或安装须预先准备依赖 wheel、构建环境及缓存；离线成功不证明远程下载过程。

需要将其他场景也放在安装环境运行时，使用 [installed.py](installed.py)：

```bash
.venv/bin/python tests/common/installed.py \
  --python "$ANY_TO_MD_INSTALLED_PYTHON" \
  --report var/quality/installed-scenarios.json
.venv/bin/python tests/common/installed.py UC-03-S3 UC-03-S4 \
  --python "$ANY_TO_MD_INSTALLED_PYTHON" \
  --report var/quality/installed-selected.json --timeout 1800
```

包装器以 `python -I` 和临时工作目录启动，只从仓库加载场景及独立检查工具；生产模块、CLI 和 Web 子进程必须来自指定安装环境，并核对旧包缺席、字体和命令入口。参数只有场景编号、`--python`、`--report`、`--timeout`；输出依赖由场景决定。安装边界检查通过也不能代替 UC-03-S1 的最终包身份与完整行为验收。

## 独立样本与检查工具

[fixtures.py](fixtures.py) / [cases.json](cases.json) 固定原始正文、顺序、图像像素、表格坐标与跨度、代码和链接目标；[docx_fixtures.py](docx_fixtures.py) 构造 OOXML 列表、评论、脚注与代码，[url_fixtures.py](url_fixtures.py) 提供网页样本。这些预期不由生产 Reader 生成。

[inspection.py](inspection.py) 为场景提供 MD、HTML、PDF 观察方法。整体与分章视图分别检查；图片按解码尺寸和 RGB 像素核对，不能以文件名代替。Markdown 经 `tables`、`fenced_code`、`toc` 与 Unicode 标题 slug 渲染后核对锚点和内容。文本、顺序与资源检查不能替代分页、布局和阅读效果复核。

以下工具可辅助产生证据，不是独立的场景测试；它们的局部通过不代表故事完成。

### 真实 EPUB 源对照

```bash
.venv/bin/python tests/common/full_book.py \
  --input "/absolute/path/to/book.epub" \
  --export "/absolute/path/to/published-export" \
  --report var/quality/private-full-book.json --pdf
```

[full_book.py](full_book.py) 直接读取源 ZIP/XML 的 OPF、spine、NCX 或 EPUB3 nav，不导入生产 Reader/Writer。它检查文本单元次数与顺序、源图片字节与顺序、导航和片段归属、元数据、目录及分章导航；PDF 与独立审计后的 HTML 全文比较。

只允许 Unicode NFKC／空白规范化，以及有明确源字体和连续编号证据的印刷行号处理；未知、冲突、条件、导入或嵌套字体不批准删号。整书文本比较不能证明每页布局或代码空白完全正确。

### 隔离历史对照

```bash
QUALITY_SOURCE_TREE="$(mktemp -d)"
git archive fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4 | tar -x -C "$QUALITY_SOURCE_TREE"
.venv/bin/python tests/common/capture.py \
  --source-tree "$QUALITY_SOURCE_TREE" \
  --revision fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4 \
  --output var/quality/new-baseline --render-pdf
```

输出目录必须是新的。[capture.py](capture.py) 拒绝归档外导入、已加载第一方模块和运行中修改代码或检查器，并记录来源与哈希；退出 `0` 只代表报告写出。该工具只适用于确实含所需旧包的归档，不能为运行它而恢复生产旧引擎。

用户书籍、完整输出和大报告保留在 Git 忽略的 `var/quality/`；跨机器复验需要相应原件和运行环境。更新项目结论前核对源码、样本、命令和报告适用范围，不从文件名、旧通过计数或历史证据继承当前验收。
