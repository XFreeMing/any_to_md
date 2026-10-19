# any_to_md

any_to_md 提供 Web、CLI 和目录批处理，将 EPUB、HTML、DOCX 与网页转换为 Markdown、HTML、PDF，保存原件、任务和产物；PDF 与图片目前通过 Web 转换，其共同 Reader 和完整交付验收仍在 [TODO](TODO.md) 中。

**行为文档入口：[场景代码与运行说明](tests/common/README.md)。** 已验收行为由 `tests/E*/UC-*.py` 的条件、操作和断言维护；其中也包含开发中的测试，是否通过以相应版本的运行报告为准。剩余需求只看 [TODO](TODO.md)，协作和开发边界见 [AGENTS](AGENTS.md)。

## 开始使用

Python 3.13+，使用 `uv` 安装并启动 Web：

```bash
uv sync
uv run any-to-md
```

默认访问 [Web](http://localhost:8888) 或 [API 说明](http://localhost:8888/docs)。配置项与默认值见 [Web 配置](web/config.py)。图片和扫描 PDF 需要外部 OCR；PDF 输出需要 WeasyPrint 原生依赖，准备方法见上方行为文档入口。

CLI 示例与参数查询：

```bash
uv run any-to-md-book --input "/path/to/book.epub" --data-dir "./var" --target markdown html pdf
uv run any-to-md-book --help
```

CLI 的 `--data-dir` 与 Web 的 `DATA_DIR` 需要显式指向同一位置才共享资料。输入与输出定义见 [能力表源码](src/any_to_md/application/capabilities.py)，参数定义见 [CLI 源码](src/any_to_md/interfaces/cli/convert_book.py)；此处不再维护一份格式或参数清单。

## 代码结构

```text
any_to_md/
├── AGENTS.md / TODO.md       # 协作边界 / 未完成需求
├── pyproject.toml / uv.lock  # 命令、分发与依赖
├── src/any_to_md/
│   ├── application/         # 转换用例、能力与任务
│   ├── domain/              # 正文、任务和产物模型
│   ├── readers/             # 输入读取；PDF 当前只有分析模块
│   ├── content/             # 共同正文、资源、样式及引用
│   ├── writers/ / export/   # 输出、导航与打包
│   ├── adapters/            # 存储、HTTP、OCR 等外部接口
│   ├── interfaces/cli/      # 命令行入口
│   └── resources/           # 渲染资源
├── web/                     # 当前 Web 入口
├── pdf_to_md/ / ocr_to_md/   # 仍在使用的过渡转换路径
├── scripts/run/             # CLI 薄启动脚本
├── tests/
│   ├── common/             # 执行器、样本、检查工具与运行说明
│   ├── E1/                 # PDF / 图片场景
│   └── E2/                 # 安装 / 数据 / 浏览器场景
├── specs/                   # 架构约束与设计理由
├── docs/conversion/         # 必要的取舍和独立证据
└── var/                     # 本地数据、运行报告与产物（Git 忽略）
```

原件、修订和历史导出属于长期资料，不按临时文件清理。`input/`、`output/` 及部分旧格式目录也可能保留本地素材和产物；目录存在不代表仍有正式转换源码。
