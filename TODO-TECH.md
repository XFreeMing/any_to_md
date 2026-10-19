# 技术验证 TODO

本文件是技术验证的唯一待办，维护尚待回答的选型、可行性、兼容性与性能问题，以及实验范围、方法、证据和决策条件。产品承诺与正式验收只在 [需求 TODO](TODO.md) 维护，分类与收尾规则见 [AGENTS](AGENTS.md)。

技术项使用 `TV-编号`，检查使用 `TV-编号-C编号`；不套用用户故事或产品 Epic，不进入 `tests/E*/UC-*.py` 及产品验收清单。技术执行成功、样本质量达标、产品验收通过分别判断。实验结论可以为不采用，不能因此降低或删除关联需求。

本轮分类调整将原 T-02-0 归为 TV-01；原来仅声明、尚未建文件的 UC-01-S7、UC-01-S8、UC-05-S5 分别归为 TV-01-C1、C2、C3。原 UC 编号不再分配给其他行为；验证范围保留于下文。

<a id="tv-01"></a>

## TV-01 — 在当前机器验证 MinerU 4 与本地小 LLM

- 状态：待开始。
- 关联需求：[US-02](TODO.md#us-02--从任一入口完整转换-pdf)；技术验证结果供选型使用，不计为需求交付。
- 当前边界：研发计划与样本预检已明确，命令实现、环境安装、模型下载及真实推理仍待执行。

目标是回答：两份真实 PDF 能否完整转换；扫描目录的文字、条目、层级和页码能否恢复；增加小 LLM 后，交付 Markdown 的标题关系改善多少、付出多少时间与内存。输出可复查的原始产物及结论，不接生产 Reader / Web / 旧 OCR，不实现分块、自研目录修复、多模型平台或正式安装包支持。实现尽量使用跨平台 Python 与 llama.cpp，当前验证只针对本机，Windows / Linux 不宣称通过。

**环境与样本前置条件**

2026-10-19 核实：Apple M4 Pro、arm64、64 GiB 内存、macOS 27.0.1，uv 0.10.11，项目 Python 3.13，磁盘可用约 125 GiB；当前项目未安装 MinerU、mineru-llama-cpp 或 Torch，未运行模型。安装前重新检查可用空间与设备，不把预检当作速度或质量实测。

| 命令绑定 | 原始输入（均在 `input/`） | 当前预检与后续动作 |
| --- | --- | --- |
| `hello-1` | `经济学原理(第10版)宏观经济学分册 ([美] N.格里高利·曼昆梁小民、梁砾 译) (z-library.sk, 1lib.sk, z-lib.sk).pdf` | 116,997,272 字节、409 页、无内嵌书签；PyMuPDF 无需修复，Poppler 正常读取，代表页可渲染。PDF 第 18–23 页为双栏扫描目录，均无文字层；部分正文有既有文字层，须另核对其可靠性。使用用户重新提供的此版本，完整源参考仍待建立。 |
| `hello-2` | `Productivity+Live+coaching+call.pdf` | 288,302 字节、15 页、有文字层、无内嵌书签；正文有图文环绕，已查看第 2 / 12 页。完整参考仍待建立；不预设它有书籍式目录或多级章节。 |

当前书籍 SHA-256 为 `a8f99123b93c237af7a43cbe53a7416ef0ec4aa2c6f270e4890e3fb14cc584c6`，专栏为 `b6507899e6ccac0d3031d1b615820f540886a66b7a1989fa528b4d4fa4a17a27`。下一轮先复核文件身份，样本原件保持不变；命令每次运行均检查原件存在、可读、页数大于零及代表页可渲染，预检失败保存诊断并停止提交。源参考以实际渲染页为准，不直接把既有 OCR 文字层当作正确答案。

**命令约定（待实现）**

用户输入的第二个 `hello-1` 按第二份样本命名为 `hello-2`；保留 `downlod` 拼写作为 `download` 的同实现别名。所有命令从源码检出使用，默认无需用户手写模型路径、环境变量或启动另一个推理服务。

| 命令 | 必须完成的工作与可观察结果 |
| --- | --- |
| `uv run mineru4 init` | 安装锁定的隔离 Python 运行环境与标题模型用的独立 llama-server，建立配置、日志与缓存目录，检查原生扩展、MPS / Metal 和可执行文件；输出实际版本、路径及缺失项。可重复运行，不下载模型权重、不修改全局 Python / Homebrew 或现有资料。 |
| `uv run mineru4 download` / `uv run mineru4 downlod` | 下载并校验 MinerU 所需小模型、VLM GGUF / mmproj，以及本地标题 LLM；完整模型复用，未完成下载可续传，失败不标记就绪。记录真实来源、revision 与文件哈希；可显式选择受支持镜像，不静默切换不同模型。 |
| `uv run mineru4 dev` | 前台启动完整 MinerU V1 API（127.0.0.1:8765）与标题 llama-server（127.0.0.1:8766），两者探活、模型预热及推理后端确认后才显示就绪、API 文档地址与日志位置。缺环境 / 权重提示相应前置命令，端口被占用明确失败；Ctrl-C 只停止本次创建的进程。 |
| `uv run mineru4 hello-1` | 对绑定书籍进行全部 409 页解析，保存 A 原始结果，再在其副本上进行 B 小 LLM 标题分级，输出两组产物与证据位置；输入无效时返回明确输入错误。 |
| `uv run mineru4 hello-2` | 对绑定专栏执行与 `hello-1` 完全相同的流程，覆盖全部 15 页；两个命令只在样本绑定和独立质量参考上不同。 |

顺序为 `init → download → dev`；保留 `dev` 终端，在另一终端执行两个 `hello`，可先验证可读的专栏。初始化或下载部分失败时保留已成功准备的内容，下一次核验后继续；`dev` / `hello` 不隐式安装、下载或自动更换后端。退出 `0` 仅表示该命令的技术工作与产物完整性通过；参数错误为 `2`，运行失败为 `1`，客户端中断为 `130`。`hello` 必须 A 完整且 B 全部分组有效应用才记 A / B 执行成功；A 为 partial / failed / canceled、产物不全，或 B 请求 / 校验 / 上下文失败时退出 `1` 并保留可取得的产物。唯一无须 B 调用的正常情况是没有标题候选：记录 `not_applicable`，A 完整时允许退出 `0`，但不计为 LLM 分级成功。样本质量是否达标由 TV-01-C1 / TV-01-C2 和对应人工证据独立判定，不能由 `hello` 的退出码推导。

**运行时与模型选择**

- 根项目仅注册轻量 `mineru4` console-script，入口放在 `src/interfaces/cli/mineru4.py`，HTTP 复用已有 httpx。新增 `scripts/mineru4/pyproject.toml` 与独立 `uv.lock`，固定 Python 3.13、`mineru==4.0.12`、`mineru-llama-cpp==0.1.3`，不将 Torch / MinerU 加入根项目依赖。`init` 使用 `uv sync --project <绝对路径> --locked --python 3.13 --no-dev`，子进程的 `UV_PROJECT_ENVIRONMENT` 指向 `var/mineru4/runtime`；后续直接调用该环境，不重复解析依赖。根 console-script 会进入 wheel，但本实验入口须检查源码与独立 manifest，离开检出目录明确提示研发环境不受支持，不冒充正式分发能力。
- 配置、模型、服务数据与二进制分别放在 `var/mineru4/` 下；子进程设置独立 `MINERU_HOME`、`MINERU_CONFIG` 和模型目录。清除继承的冲突 `MINERU_*` 后显式指定 `MINERU_MODEL_SOURCE=local`、`MINERU_MODEL_SMALL_BACKEND=torch`、`MINERU_MODEL_VLM_ENGINE=llama-cpp`、`MINERU_LLAMA_CPP_BACKEND=metal`，不使用全局 `~/.mineru` 或远端 VLM。记录小模型实际设备与 VLM Metal 后端；正常的 CPU 解码 / 预处理不算降级，但不能悄悄改成 CPU 模型或其他推理引擎。
- MinerU 使用 `standard` 档，下载官方注册表指定的 `opendatalab/MinerU-4_models_torch` 与 `jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF` 中的 Q8_0 主权重和同名 mmproj；两份 GGUF 合计约 1.24 GB，另有小模型和依赖。调用 `mineru-kit models download --tier standard --small-backend torch --vlm-engine llama-cpp --source huggingface`，再执行同参数的 `models verify`。`models show` 仅展示状态，不能代替校验；官方 CLI 未提供 revision 锁定参数，首次下载须记录实际权重身份，不因 Python 包已锁定就宣称模型版本已锁定。
- 标题 LLM 首选官方 `Qwen/Qwen3-4B-GGUF` 的 `Qwen3-4B-Q4_K_M.gguf`，约 2.50 GB，Apache-2.0，仅文本输入；使用独立 llama.cpp server 与 Metal，不复用 MinerU 专用 VLM 作为通用聊天模型。固定 revision `bc640142c66e1fdd12af0bd68f40445458f3869b`、文件 SHA-256 `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`。不下载视觉投影文件、不调用云 API；先测 4B，只有实测资源或速度不合适才另评估 Qwen3-1.7B，不默认下载备选模型。
- 标题 server 初始上下文 32,768、并发 1，关闭 thinking，固定模型别名 `mineru4-title`。`init` 安装官方 `b11724` 的 `llama-b11724-bin-macos-arm64.tar.gz`，核对 SHA-256 `4e3c2951302b5fcee8d65327c6e2b28c382d4be87d9ca3af1346a7c092cd3335`；这是独立二进制，不再建第二份 Python 环境。启动参数为 `--ctx-size 32768 --parallel 1 --gpu-layers all --jinja --reasoning off --no-context-shift`，模型用本地 `-m` 路径；不追随 latest、不自动丢弃早期上下文。`--reasoning-format none` 不代表关闭 thinking，须用真实请求核验。

版本与行为依据：[MinerU 4.0.12](https://pypi.org/project/mineru/4.0.12/)、[固定版本模型注册表](https://github.com/opendatalab/MinerU/blob/mineru-4.0.12-released/mineru/model/registry.py)、[模型下载与后端配置](https://opendatalab.github.io/MinerU/usage/model_source/)、[mineru-llama-cpp](https://github.com/opendatalab/mineru-llama-cpp)、[Qwen3-4B GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF)、[llama.cpp b11724](https://github.com/ggml-org/llama.cpp/releases/tag/b11724)及其[服务参数](https://github.com/ggml-org/llama.cpp/blob/b11724/tools/server/README.md)。实现时校验固定版本的实际接口，不套用 MinerU 2.x 命令。

**同一次识别上的 A / B 对照**

1. A 使用 MinerU `standard` + `ocr_mode=auto` 完整解析，显式关闭 `llm_aided.features.title_leveling` 和 `cross_page_table_cell_merge`。API 以 `--allow-local-source --concurrency 1 --retention-seconds 0 --preload-models` 启动，只监听回环；预热失败可能仍有监听端口，必须核对 `/v1/health`、`/v1/tiers` 与启动日志。
2. `hello` 用 httpx 提交 `/v1/parse/jobs`，文件来源为本机绝对路径、`page_range="all"`，请求 `markdown`、`middle_json`、`structured_content`、`zip`。保存 job ID、轮询真实终态，检查每个文件状态并从 `/v1/files/{file_id}/content` 下载全部请求产物。不得使用默认只解析前十页的命令，也不使用内部约一小时等待上限的 SDK 便利方法；HTTP 超时与解析失败分别记录。轮询显示进度，断连 / 中断保留已受理 job 与已有证据，不伪造取消或成功；本轮不承诺服务重启恢复。
3. A 的 MD / JSON / ZIP 原样保留。B 从另一份 typed MiddleJson 实例开始，只开启上游标题分级；`llm_aided` 显式配置 `base_url=http://127.0.0.1:8766/v1`、`model=mineru4-title`、非空的本地占位 `api_key`、`max_concurrency=1`、`enable_thinking=null`，thinking 由 server 关闭，跨页表格合并仍关闭。不再执行 OCR，不改写正文、补标题、纠错文字或修复目录。实验 helper 在隔离环境运行，使用固定版本 `ParseResult.from_json`、`aio_apply_llm_aided_postprocess(..., client=...)`，再用官方 `markdown()` / `to_json()` / `structured_content()` 导出 B，按原字节及相对路径携带 A 的图片；不新增自己的 Markdown Writer，也不假设省略图片 base64 的 JSON 能直接 `save()` 出完整资源。
4. 使用现有 client 注入点做薄观察：原样调用上游 `request_validated_json`，保存分组标题、实际 prompt 及其哈希、经过上游校验的返回映射 / 失败、耗时及应用前后 level，不修改 prompt、温度、重试或 validator；在同一个异步生命周期中关闭注入 client。上游三次尝试仍失败时可能只记日志并保留旧层级，整个 PDF job 仍成功，因此 B 任一预期分组未取得有效映射时必须单列失败；保留 A，整体命令不能冒充 A / B 全部成功。合法映射的级别未变化也算实际调用成功；没有标题候选时保存“不适用”记录，由独立源参考判断正常无标题还是漏识别，不计为一次成功的 LLM 分级。
5. 分级只在完整文档执行，按已有 `DocTitleBlock` 划组，将各组 `ParagraphTitleBlock` 的序号与文字交给 LLM；没有提供目录页、页码、字号或正文。文档标题保留一级，段落标题映射到二至六级；它不会恢复遗漏的标题，也不能纠正错误的文档分组。记录上下文超限、截断和校验失败，不静默截断标题或临时发明分块算法。已接受的映射仍可能语义错误，须与独立目录 / 正文参考核对。

API 与内部扩展点依据：[HTTP API](https://opendatalab.github.io/MinerU/usage/http_api/)、[固定版本 ParseResult](https://github.com/opendatalab/MinerU/blob/mineru-4.0.12-released/mineru/parser/base.py)、[标题分级](https://github.com/opendatalab/MinerU/blob/mineru-4.0.12-released/mineru/backend/postprocess/title_leveling.py)、[LLM 客户端](https://github.com/opendatalab/MinerU/blob/mineru-4.0.12-released/mineru/backend/postprocess/llm_client.py)。内部调用隔离在一个实验 helper，版本升级先核实接口；不修改已安装的上游源码。

**证据与实施顺序**

- 每次 `hello` 写入新的 `var/quality/mineru4/<hello编号>/<run-id>/`，保留 A / B、资源、原始响应、逐阶段日志与 manifest；下载使用临时文件加原子落盘，ZIP 解包复用安全路径校验，检查 MD 相对资源引用。原件、既有产物、模型和完整书籍不提交 Git。
- manifest 记录源码 / 依赖锁 / 配置 / 模型 / 输入哈希、实际版本与设备、输入页数与处理页范围、任务状态、分级调用和应用结果。分别计安装 / 下载、模型加载、PDF 解析、标题分级、导出耗时；报告真实页速和有测量方法的进程内存，不预先承诺速度或把进程 RSS 当作完整 GPU 占用。A / B 复用一次 OCR，只比较标题后处理增量，不称为两次独立全流程跑分；两个服务常驻时记录各进程与总体峰值，不把 A 阶段总内存称为 MinerU 单独运行的最低需求。
- 先确定下列 TV-01-C1～C3 的独立参考、操作与判定，再实现命令；用 `scripts/mineru4/verify.py` 承接实验检查，复用真实服务和模型缓存，不为每项重新下载。实验报告独立绑定 `TODO-TECH.md`、实验入口与 helper、`scripts/mineru4/pyproject.toml`、独立 `uv.lock`、模型、配置、输入及产物身份；运行时 manifest 作为证据核对，不纳入运行前代码指纹。可复用 `tests/common` 的纯检查工具与人工证据身份校验方法，但不建立 `tests/E*/UC-*.py`，不写入产品验收报告或通过计数。
- 完成条件：TV-01-C1～C3 的必要实验与人工复核均有可追溯证据，并明确建议与适用边界；模型质量不达标可以形成“不适合”的结论，但报告不得改写为质量通过。缺实验、缺资料或缺复核仍保留待办。结论复核后仅将必要取舍与报告定位写入 [转换依据](specs/evidence.md)，不归档整份研发计划。
- 实施顺序：命令生命周期 → 专栏全量 A / B → 书籍全量及扫描目录 A / B → 目录、正文、资源与性能复核。按本轮报告给出“可采用 / 需要补目录结构能力 / 不适合”的有证据结论，保留失败与阻塞范围。缺真实推理、缺人工证据或只跑部分页都不能宣布两份样本验证完成；实验结论也不代表 US-02 正式入口已交付。
- 剩余准备：建立两份样本的独立参考；实施环境初始化、模型下载与真实推理，核验标题 LLM 兼容性及两组效果。2026-10-19 的文件预检只证明当前样本可进入实验，不证明模型效果或全书内容完整。

### TV-01-C1 — 真实书籍的扫描目录与标题分级效果可核对

- 检查归属：TV-01；通过实验检查脚本验证书籍样本。
- Given：绑定书籍共 409 页，PDF 第 18–23 页为真实扫描目录；转换前从原图独立标定全部双栏目录条目及正文对应标题，记录文字、编号、父子关系、印刷页码和实际 PDF 页。PDF 第 16–17 页是学习指南，不能混入本分册目录；正式目录从第 8 篇 / 第 24 章开始，不补造前面的篇章。不同前言 / 目录 / 正文页码段分别映射，还须区分页边的英文原书页码，不假设只有一个页码偏移。
- When：通过 `hello-1` 完整转换全部 409 页并保存 A / B，对全部六页目录按先左栏后右栏的源阅读顺序单独核对；局部目录调试不能替代全书结果。
- Then：逐页核对覆盖，逐项记录目录文字 / 编号 / 页码错误、漏项、多项、顺序和父子关系；将目录条目匹配到正文实际标题及 Markdown 层级，记录错挂、断层和漏标题，不能只证明目录页能读成普通文字。单独报告 A / B 的正确数量与分母，不能用平均分掩盖已确认的结构错误。
- Then：对正文跨页衔接、代表性章 / 节 / 子节、页眉页脚与实际存在的表格 / 公式 / 图片进行有源页定位的阅读核验，图表至少保留可读内容、图题和位置；图表图像保留不等于已恢复其数据。B 只允许上游标题级别变化，正文、标题文字、顺序和资源保持 A 的内容身份。质量错误为失败，缺原件或参考为阻塞；都不能以解析任务的 completed 状态覆盖。
- 当前缺口：全部目录与正文的独立参考、真实 A / B 产物及人工证据。这里不替代 UC-01-S6 的多样本正式 Reader 验收。

### TV-01-C2 — 真实专栏 PDF 完整保留图文与实际层级

- 检查归属：TV-01；通过实验检查脚本验证专栏样本，复用上述 A / B 流程。
- Given：绑定的 Productivity PDF 共 15 页，已逐页独立确认正文顺序、实际标题、强调 / 引用文字、图文环绕和资源位置；不存在的目录、多篇边界或表格不作为样本已覆盖能力。
- When：通过 `hello-2` 完整转换，保留同一次识别的 A 原始输出及 B 本地小 LLM 分级输出。
- Then：封面、正文及末页均有可核对处理结果，正文没有漏段、重复或错序，实际图片可打开且位置正确；没有页书签不等于没有可识别正文，不按页造章节、不将大字引用或强调句强行升级为标题。
- Then：逐项对照两组标题关系，核实 B 的实际调用与映射是否应用；有效映射未改变级别记为“已分级、无变化”，仅无候选时为不适用，请求或映射失败为失败。是否改善由源参考判定，不能伪造层级提升。完整 15 页阅读证据与分阶段耗时归本项检查，未验证的书籍目录能力不由本项检查代替。

### TV-01-C3 — 本机 MinerU 实验命令可重复准备并明确报告失败

- 检查归属：TV-01；通过实验检查脚本验证研发命令，不扩展 US-07 的正式 CLI 范围。
- Given：源码检出与独立实验目录，保留输入、既有产物和其他服务；模型未就绪与已就绪两种状态均有真实条件。
- When：用真实子进程依次运行 `init`、`download` / `downlod`、`dev` 和两个 `hello`，并覆盖重复准备、下载中断 / 文件缺失、未启动服务、端口占用、不可读输入和客户端中断。
- Then：首次能完成真实准备与服务推理，重复操作复用校验通过的环境 / 模型；失败指向正确阶段、不标记虚假就绪、不覆盖原件、不终止他人进程，产物不完整时不退出成功。下载别名行为一致，两份样本绑定不串文件；不可读输入使用独立异常样本，不损坏用户原件，不代替 TV-01-C1 的真实内容检查。
- Then：B 的 LLM 超时、无效 / 不全映射、上下文超限及无标题候选均有准确结果，A 保留且不被覆盖；异常协议可用明确标注的受控响应验证，正常路径仍必须使用真实小模型。执行成功、内容质量失败和缺人工证据分别可观察，质量判断复用 TV-01-C1 / TV-01-C2。
