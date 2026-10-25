# 阅读与运行场景

## 从代码了解行为

按用户目标进入 [E1：书籍与长篇资料结构化](../E1/)、[E2：办公与扫描文档结构化](../E2/)、[E4：可靠完成转换](../E4/) 或 [E5：长期保留与复用资料](../E5/)，选择 `UC-*.py`。先读文件开头的用户意图与场景条件，再读 `run()` 中的样本、操作、断言及 `checks`；公共驱动和独立检查器位于 [common](./)。用下文的 `--list` 从实际文件和 TODO 获取清单，不另维护完成索引。

场景代码表达约定，运行报告证明具体版本与范围的执行结果。查看报告的 `scope`、`results`、`checks`、阻塞原因及证据，不能仅凭文件存在或一项通过判断整个功能已验收。需求进入测试和移出 TODO 的规则见 [AGENTS](../../AGENTS.md)，剩余产品范围见 [需求 TODO](../../TODO.md)。

本执行器从统一 [TODO](../../TODO.md) 的产品 Epic 中发现 `Scenario` 声明，并保留 `tests/E*/UC-*.py` 回归；[技术验证分区](../../TODO.md#technical-validation)中的 TV 项目使用各自指定的独立入口与报告，可复用公共检查工具，但不进入产品场景发现、验收报告或通过数。规划与执行交接也不作为验收场景。

例如，[并发转换](../E4/UC-03-S3.py) 描述多个任务如何保留各自产物，[上传拒绝](../E4/UC-04-S2.py) 用 `cases` 表表达两种拒绝条件；不在本说明中重复它们的验收步骤或维护通过状态。

## 环境与运行

在仓库根目录使用 Python 3.13+：

```bash
uv sync --group test
.venv/bin/python tests/common/run.py --list
.venv/bin/python tests/common/run.py --report var/quality/scenarios.json
.venv/bin/python tests/common/run.py UC-03-S3 UC-03-S4 \
  --report var/quality/selected-scenarios.json
.venv/bin/python tests/E2/UC-02-S2.py \
  --report var/quality/single-scenario.json
```

单个场景文件也可直接执行，仍通过同一个公共执行器完成断言、证据检查和报告；测试工具使用上述脚本入口，不依赖环境中同名 `tests` 包的模块查找结果。

不指定编号时运行 TODO 中的场景与全部 Epic 中的回归文件；移出 TODO 不会丢失测试。指定编号或直接运行 UC 文件时，报告的 `scope` 为 `selected`。`--list` 只显示路径与缺文件状态，并检查编号和场景目录归属，不运行行为。退出码 `0` 表示所选范围全部通过，任意失败或阻塞返回 `1`；不能使用 Python `-O` / `-OO` 关闭断言。

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

[fixtures.py](fixtures.py)、[cases.json](cases.json)、[docx_fixtures.py](docx_fixtures.py) 和 [url_fixtures.py](url_fixtures.py) 保存独立编写的源样本与预期；[inspection.py](inspection.py) 提供产物观察方法。具体断言以调用它们的 UC 文件为准，不能由生产 Reader 生成期望值。

以下工具可辅助产生证据，不是独立的场景测试；它们的局部通过不代表故事完成。

### 真实 EPUB 源对照

```bash
.venv/bin/python tests/common/full_book.py \
  --input "/absolute/path/to/book.epub" \
  --export "/absolute/path/to/published-export" \
  --report var/quality/private-full-book.json --pdf
```

[full_book.py](full_book.py) 独立读取源 EPUB 并与已导出文件对照，具体观察项与规范化边界以其代码为准。它是手动辅助工具，当前没有 UC 调用；整书文本比较不能证明每页布局或代码空白完全正确。

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
