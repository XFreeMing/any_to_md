# any_to_md Web 服务设计规划

## 1. 项目概述

将现有的文档格式转换脚本升级为一个 Web 服务，提供可视化界面，支持多种文档格式的上传、转换和下载。

### 1.1 核心功能

- **文件上传**: 拖拽或选择文件上传
- **格式转换**: 选择目标格式，执行转换
- **结果下载**: 转换完成后自动下载或手动下载
- **转换进度**: 实时显示转换状态

### 1.2 技术栈

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 后端 | FastAPI | 异步高性能，自带 OpenAPI 文档 |
| 前端 | 原生 HTML/CSS/JS | 无需构建，简单直接 |
| 任务队列 | 内存队列 (asyncio) | 轻量级，适合单机部署 |
| 文件存储 | 本地临时目录 | 自动清理过期文件 |

---

## 2. 支持的格式

### 2.1 输入格式

| 格式 | 扩展名 | 实现方式 |
|------|--------|----------|
| EPUB | `.epub` | 现有 `epub_to_md`、`epub_to_html` 模块 |
| PDF (文字版) | `.pdf` | 新增 `pdf_to_md` 模块 (使用 `pymupdf4llm`) |
| PDF (扫描版) | `.pdf` | 新增 `ocr_to_md` 模块 (调用 DeepSeek OCR API) |
| Word | `.docx` | 新增 `docx_to_md` 模块 (使用 `mammoth`) |
| 网页 | URL | 新增 `url_to_md` 模块 (使用 `trafilatura`) |
| 图片 | `.png/.jpg/.jpeg/.gif/.bmp/.webp` | 新增 `ocr_to_md` 模块 (调用 DeepSeek OCR API) |

### 2.2 输出格式

| 格式 | 扩展名 | 实现方式 |
|------|--------|----------|
| Markdown (单文件) | `.md` | 现有转换逻辑 |
| Markdown (分章节) | `.zip` | 多文件打包为 ZIP |
| HTML | `.html` | 现有 `epub_to_html` 或直接转换 |
| PDF | `.pdf` | 现有 `html_to_pdf` (WeasyPrint) |

### 2.3 转换矩阵

```text
输入 → 输出支持:

           │ MD(单) │ MD(章节) │ HTML │ PDF │
───────────┼────────┼──────────┼──────┼─────┤
EPUB       │   ✓    │    ✓     │  ✓   │  ✓  │
PDF(文字)  │   ✓    │    ✗     │  ✓   │  ✗  │
PDF(扫描)  │   ✓    │    ✗     │  ✓   │  ✓  │
Word       │   ✓    │    ✗     │  ✓   │  ✓  │
URL        │   ✓    │    ✗     │  ✓   │  ✓  │
图片       │   ✓    │    ✗     │  ✓   │  ✓  │
```

---

## 3. 系统架构

```text
┌─────────────────────────────────────────────────────────────┐
│                        Browser                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Web UI (index.html)                     │    │
│  │  - 文件上传区 (拖拽/选择)                            │    │
│  │  - 格式选择器                                        │    │
│  │  - 转换进度条                                        │    │
│  │  - 下载按钮                                          │    │
│  └─────────────────────────────────────────────────────┘    │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Server                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ /api/upload  │  │ /api/convert │  │ /api/download│       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                 │                 │                │
│         ▼                 ▼                 ▼                │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Conversion Service                      │    │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐    │    │
│  │  │epub_to_*│ │pdf_to_md│ │docx_to_*│ │url_to_md│    │    │
│  │  └─────────┘ └────┬────┘ └─────────┘ └─────────┘    │    │
│  │                   │                                  │    │
│  │         ┌─────────┴─────────┐                       │    │
│  │         ▼                   ▼                       │    │
│  │  ┌────────────┐     ┌─────────────┐                 │    │
│  │  │ pymupdf4llm│     │  ocr_to_md  │ ←───────────────┼────┤
│  │  │ (文字PDF)  │     │ (扫描PDF/   │                 │    │
│  │  └────────────┘     │  图片)      │                 │    │
│  │                     └──────┬──────┘                 │    │
│  └─────────────────────────────────────────────────────┘    │
│                               │                              │
│                               ▼                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              File Storage (temp/)                    │    │
│  │  - 上传文件: temp/uploads/{task_id}/                 │    │
│  │  - 输出文件: temp/outputs/{task_id}/                 │    │
│  │  - 自动清理: 1小时后删除                             │    │
│  └─────────────────────────────────────────────────────┘    │
└────────────────────────────────┬────────────────────────────┘
                                 │ HTTP (异步)
                                 ▼
┌─────────────────────────────────────────────────────────────┐
│              DeepSeek OCR API (外部服务)                     │
│              http://localhost:9122                           │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  POST /api/v1/ocr/process                            │    │
│  │  - 输入: PDF/图片文件                                │    │
│  │  - 输出: ZIP (result.md + images/)                   │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3.1 PDF 类型检测与转换流程

```text
                    ┌─────────────┐
                    │  上传 PDF   │
                    └──────┬──────┘
                           │
                           ▼
                ┌─────────────────────┐
                │  检测 PDF 类型       │
                │  (提取文字数量)      │
                └──────────┬──────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
    ┌─────────────────┐      ┌─────────────────┐
    │ 文字数 >= 阈值   │      │  文字数 < 阈值   │
    │ (文字版 PDF)    │      │  (扫描版 PDF)   │
    └────────┬────────┘      └────────┬────────┘
             │                        │
             ▼                        ▼
    ┌─────────────────┐      ┌─────────────────┐
    │   pymupdf4llm   │      │  DeepSeek OCR   │
    │   直接提取文字   │      │   API 识别      │
    └────────┬────────┘      └────────┬────────┘
             │                        │
             └────────────┬───────────┘
                          │
                          ▼
                ┌─────────────────┐
                │   Markdown 文本  │
                └────────┬────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │ 输出 MD  │  │ 转 HTML  │  │ 转 PDF   │
    └──────────┘  └──────────┘  └──────────┘
```

**检测逻辑 (pdf_to_md/detector.py):**
1. 使用 `pymupdf` 打开 PDF
2. 提取前 N 页的文字内容
3. 统计有效字符数量
4. 若字符数低于阈值 (如每页平均 < 50 字符)，判定为扫描版

---

## 3.2 OCR 模块设计 (ocr_to_md/)

### 调用流程

```python
# ocr_to_md/converter.py

async def convert_with_ocr(
    file_path: Path,
    output_dir: Path,
    ocr_api_url: str = "http://localhost:9122/api/v1/ocr/process"
) -> Path:
    """
    调用 DeepSeek OCR API 将 PDF/图片转换为 Markdown

    1. 上传文件到 OCR API
    2. 接收 ZIP 响应
    3. 解压获取 result.md 和 images/
    4. 返回 Markdown 文件路径
    """
```

### 输入输出

| 输入 | 输出 |
|------|------|
| PDF (扫描版) | Markdown + 图片目录 |
| PNG/JPG/JPEG/GIF/BMP/WEBP | Markdown + 图片目录 |

### 错误处理

- OCR 服务不可用 → 返回错误提示，建议使用文字版 PDF
- 超时 (大文件) → 增加超时时间，支持重试
- 识别失败 → 返回原始错误信息

---

## 4. API 设计

### 4.1 上传文件

```http
POST /api/upload
Content-Type: multipart/form-data

Request:
  file: <binary>

Response:
{
  "task_id": "uuid-xxxx",
  "filename": "book.epub",
  "input_format": "epub",
  "available_outputs": ["md_single", "md_chapters", "html", "pdf"]
}
```

### 4.2 提交 URL (网页抓取)

```http
POST /api/fetch
Content-Type: application/json

Request:
{
  "url": "https://example.com/article"
}

Response:
{
  "task_id": "uuid-xxxx",
  "title": "Article Title",
  "input_format": "url",
  "available_outputs": ["md_single", "html", "pdf"]
}
```

### 4.3 执行转换

```http
POST /api/convert
Content-Type: application/json

Request:
{
  "task_id": "uuid-xxxx",
  "output_format": "md_single"
}

Response:
{
  "task_id": "uuid-xxxx",
  "status": "processing"
}
```

### 4.4 查询状态

```http
GET /api/status/{task_id}

Response:
{
  "task_id": "uuid-xxxx",
  "status": "completed",  // pending | processing | completed | failed
  "progress": 100,
  "message": "转换完成",
  "download_url": "/api/download/uuid-xxxx"
}
```

### 4.5 下载文件

```http
GET /api/download/{task_id}

Response:
  Content-Disposition: attachment; filename="book.md"
  <file binary>
```

---

## 5. 目录结构

```text
any_to_md/
├── .env                          # 环境变量 (不提交到 git)
├── .env.example                  # 环境变量模板 (提交到 git)
├── .gitignore                    # 更新: 添加 .env
│
├── web/                          # 新增: Web 服务模块
│   ├── __init__.py
│   ├── app.py                    # FastAPI 应用主入口
│   ├── routes/                   # API 路由
│   │   ├── __init__.py
│   │   ├── upload.py             # 上传相关接口
│   │   ├── convert.py            # 转换相关接口
│   │   └── download.py           # 下载相关接口
│   ├── services/                 # 业务逻辑
│   │   ├── __init__.py
│   │   ├── converter.py          # 统一转换调度器
│   │   ├── file_manager.py       # 文件管理和清理
│   │   └── task_manager.py       # 任务状态管理
│   ├── static/                   # 静态文件
│   │   ├── index.html            # 主页面
│   │   ├── style.css             # 样式
│   │   └── app.js                # 前端逻辑
│   └── config.py                 # 配置项 (从 .env 读取)
│
├── pdf_to_md/                    # 新增: PDF 转换模块
│   ├── __init__.py
│   ├── converter.py              # 主转换器 (自动选择方式)
│   └── detector.py               # PDF 类型检测器
│
├── ocr_to_md/                    # 新增: OCR 转换模块 (调用 DeepSeek API)
│   ├── __init__.py
│   └── converter.py              # OCR API 客户端
│
├── docx_to_md/                   # 新增: Word 转换模块
│   ├── __init__.py
│   └── converter.py
│
├── url_to_md/                    # 新增: URL 抓取模块
│   ├── __init__.py
│   └── converter.py
│
├── epub_to_md/                   # 现有模块 (保持不变)
├── epub_to_html/
├── html_to_md/
├── html_to_pdf/
│
├── specs/                        # 设计文档
│   └── 0001-overview.md          # 本文档
│
└── pyproject.toml                # 更新依赖
```

---

## 6. 前端界面设计

### 6.1 页面布局

```
┌────────────────────────────────────────────────────────┐
│                    Any to Markdown                      │
│                 文档格式转换工具                         │
├────────────────────────────────────────────────────────┤
│                                                         │
│  ┌───────────────────────────────────────────────────┐ │
│  │                                                   │ │
│  │     📄 拖拽文件到此处，或点击选择文件              │ │
│  │                                                   │ │
│  │     支持: EPUB, PDF, DOCX                         │ │
│  │                                                   │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  ─────────────────── 或者 ───────────────────────────  │
│                                                         │
│  输入网页 URL: [________________________] [抓取]        │
│                                                         │
├────────────────────────────────────────────────────────┤
│                                                         │
│  输出格式:  ◉ Markdown (单文件)                        │
│             ○ Markdown (分章节)                        │
│             ○ HTML                                     │
│             ○ PDF                                      │
│                                                         │
│  [               开始转换               ]               │
│                                                         │
├────────────────────────────────────────────────────────┤
│                                                         │
│  状态: ████████████░░░░░░░░ 60%                        │
│  正在转换第 3/5 章...                                   │
│                                                         │
│  [               下载结果               ]               │
│                                                         │
└────────────────────────────────────────────────────────┘
```

### 6.2 交互流程

```
1. 用户上传文件 或 输入 URL
      │
      ▼
2. 后端识别格式，返回可用输出选项
      │
      ▼
3. 用户选择目标格式，点击"开始转换"
      │
      ▼
4. 前端轮询状态，显示进度条
      │
      ▼
5. 转换完成，自动触发下载 (或显示下载按钮)
```

---

## 7. 新增依赖

```toml
# pyproject.toml 更新

dependencies = [
    # 现有依赖
    "beautifulsoup4>=4.14.2",
    "ebooklib>=0.20",
    "markdownify>=1.2.0",
    "weasyprint>=61.0",

    # 新增: Web 框架
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "python-multipart>=0.0.12",  # 文件上传支持
    "pydantic-settings>=2.0.0",  # 环境变量配置

    # 新增: PDF 转换
    "pymupdf4llm>=0.0.17",       # PDF → Markdown

    # 新增: Word 转换
    "mammoth>=1.8.0",            # DOCX → HTML/MD

    # 新增: 网页抓取
    "trafilatura>=2.0.0",        # 网页内容提取
    "httpx>=0.28.0",             # 异步 HTTP 客户端
]
```

**.env.example 模板**
```bash
# OCR API 配置
OCR_API_URL=http://localhost:9122/api/v1/ocr/process
OCR_TIMEOUT=600

# PDF 检测阈值
PDF_TEXT_THRESHOLD=50

# 文件存储
TEMP_DIR=temp
MAX_UPLOAD_SIZE=104857600
```

---

## 8. 实现计划

### Phase 1: 基础框架 (核心)

1. **创建 FastAPI 应用结构**
   - `web/app.py` - 应用入口
   - `web/config.py` - 配置项
   - `web/routes/` - API 路由框架

2. **实现文件管理**
   - `web/services/file_manager.py` - 上传/下载/清理
   - `web/services/task_manager.py` - 任务状态管理

3. **创建前端界面**
   - `web/static/index.html` - 主页面
   - `web/static/style.css` - 样式
   - `web/static/app.js` - 交互逻辑

### Phase 2: 集成现有转换器

4. **统一转换接口**
   - `web/services/converter.py` - 统一调度器
   - 集成 `epub_to_md`, `epub_to_html`, `html_to_pdf`

5. **实现 API 端点**
   - `/api/upload` - 文件上传
   - `/api/convert` - 执行转换
   - `/api/status/{task_id}` - 状态查询
   - `/api/download/{task_id}` - 文件下载

### Phase 3: 扩展输入格式

1. **PDF 转换模块**
   - `pdf_to_md/detector.py` - PDF 类型检测 (文字版 vs 扫描版)
   - `pdf_to_md/converter.py` - 智能转换器 (自动选择 pymupdf4llm 或 OCR)

2. **OCR 模块 (DeepSeek API 集成)**
   - `ocr_to_md/converter.py` - 调用 OCR API，处理 ZIP 响应
   - 支持 PDF (扫描版) 和图片文件

3. **Word 转换模块**
   - `docx_to_md/converter.py` - 使用 mammoth

4. **URL 抓取模块**
   - `url_to_md/converter.py` - 使用 trafilatura

### Phase 4: 完善与优化

1. **错误处理与日志**
2. **文件自动清理机制**
3. **样式美化与响应式设计**
4. **OCR 服务健康检查**

---

## 9. 启动方式

```bash
# 开发模式
uv run uvicorn web.app:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uv run uvicorn web.app:app --host 0.0.0.0 --port 8000 --workers 4
```

访问 http://localhost:8000 即可使用。

---

## 10. 关键文件清单

| 文件路径 | 说明 | 优先级 |
|----------|------|--------|
| `.env.example` | 环境变量模板 | P0 |
| `web/app.py` | FastAPI 主入口 | P0 |
| `web/config.py` | 配置项 (从 .env 读取) | P0 |
| `web/services/converter.py` | 转换调度器 | P0 |
| `web/services/file_manager.py` | 文件管理 | P0 |
| `web/services/task_manager.py` | 任务状态 | P0 |
| `web/routes/upload.py` | 上传 API | P0 |
| `web/routes/convert.py` | 转换 API | P0 |
| `web/routes/download.py` | 下载 API | P0 |
| `web/static/index.html` | 前端页面 | P0 |
| `web/static/style.css` | 样式 | P1 |
| `web/static/app.js` | 前端逻辑 | P0 |
| `pdf_to_md/converter.py` | PDF 智能转换器 | P1 |
| `pdf_to_md/detector.py` | PDF 类型检测 | P1 |
| `ocr_to_md/converter.py` | OCR API 客户端 | P1 |
| `docx_to_md/converter.py` | Word 转换 | P1 |
| `url_to_md/converter.py` | URL 抓取 | P1 |
| `pyproject.toml` | 依赖更新 | P0 |

---

## 11. OCR API 集成规范

### 11.1 配置项

**环境变量 (.env)**
```bash
# .env
OCR_API_URL=http://localhost:9122/api/v1/ocr/process
OCR_TIMEOUT=600
```

**配置类 (使用 pydantic-settings)**
```python
# web/config.py

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # OCR API 配置 (从 .env 读取)
    OCR_API_URL: str = "http://localhost:9122/api/v1/ocr/process"
    OCR_TIMEOUT: int = 600  # 10分钟超时 (大文件)
    OCR_RETRY_COUNT: int = 2

    # PDF 检测阈值
    PDF_TEXT_THRESHOLD: int = 50  # 每页平均字符数低于此值视为扫描版

    # 文件存储
    TEMP_DIR: str = "temp"
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
```

### 11.2 OCR 客户端实现

```python
# ocr_to_md/converter.py

import httpx
import zipfile
from pathlib import Path

async def convert_with_ocr(
    file_path: Path,
    output_dir: Path,
    api_url: str = "http://localhost:9122/api/v1/ocr/process",
    timeout: int = 600
) -> tuple[Path, Path | None]:
    """
    调用 DeepSeek OCR API 转换文件

    Args:
        file_path: 输入文件路径 (PDF 或图片)
        output_dir: 输出目录
        api_url: OCR API 地址
        timeout: 超时时间 (秒)

    Returns:
        (markdown_path, images_dir) - Markdown 文件路径和图片目录
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f)}
            response = await client.post(api_url, files=files)

        if response.status_code != 200:
            raise Exception(f"OCR API 错误: {response.status_code} - {response.text}")

        # 保存并解压 ZIP
        zip_path = output_dir / "ocr_result.zip"
        zip_path.write_bytes(response.content)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(output_dir)

        zip_path.unlink()  # 删除 ZIP

        md_path = output_dir / "result.md"
        images_dir = output_dir / "images"

        return md_path, images_dir if images_dir.exists() else None
```

### 11.3 PDF 类型检测

```python
# pdf_to_md/detector.py

import pymupdf

def is_scanned_pdf(pdf_path: Path, threshold: int = 50, sample_pages: int = 5) -> bool:
    """
    检测 PDF 是否为扫描版 (图片型)

    Args:
        pdf_path: PDF 文件路径
        threshold: 每页平均字符数阈值
        sample_pages: 采样页数

    Returns:
        True 如果是扫描版，False 如果是文字版
    """
    doc = pymupdf.open(pdf_path)
    total_chars = 0
    pages_to_check = min(len(doc), sample_pages)

    for i in range(pages_to_check):
        page = doc[i]
        text = page.get_text()
        total_chars += len(text.strip())

    doc.close()

    avg_chars = total_chars / pages_to_check if pages_to_check > 0 else 0
    return avg_chars < threshold
```

### 11.4 统一 PDF 转换入口

```python
# pdf_to_md/converter.py

from .detector import is_scanned_pdf
from ocr_to_md import convert_with_ocr
import pymupdf4llm

async def convert_pdf_to_markdown(
    pdf_path: Path,
    output_dir: Path,
    force_ocr: bool = False
) -> Path:
    """
    智能 PDF 转 Markdown

    自动检测 PDF 类型，选择合适的转换方式
    """
    if force_ocr or is_scanned_pdf(pdf_path):
        # 扫描版 → OCR
        md_path, _ = await convert_with_ocr(pdf_path, output_dir)
        return md_path
    else:
        # 文字版 → pymupdf4llm
        md_content = pymupdf4llm.to_markdown(str(pdf_path))
        md_path = output_dir / f"{pdf_path.stem}.md"
        md_path.write_text(md_content, encoding="utf-8")
        return md_path
```
