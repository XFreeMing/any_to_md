"""Unified conversion service that dispatches to appropriate converters."""

import zipfile
from pathlib import Path
from typing import Callable, Optional

from ..config import settings
from .file_manager import FileManager
from .task_manager import Task, TaskStatus, task_manager


class ConversionService:
    """Handles document conversion dispatching."""

    def __init__(self, task: Task, file_manager: FileManager):
        self.task = task
        self.file_manager = file_manager

    def update_progress(self, progress: int, message: str):
        """Update task progress."""
        task_manager.update_task(
            self.task.task_id,
            progress=progress,
            message=message,
        )

    async def convert(self, output_format: str) -> Path:
        """Execute conversion based on input and output formats."""
        input_format = self.task.input_format
        input_path = self.file_manager.get_upload_path(self.task.filename)

        task_manager.update_task(
            self.task.task_id,
            status=TaskStatus.PROCESSING,
            progress=10,
            message="Starting conversion...",
        )

        try:
            # Route to appropriate converter
            if input_format == "epub":
                output_path = await self._convert_epub(input_path, output_format)
            elif input_format == "pdf":
                output_path = await self._convert_pdf(input_path, output_format)
            elif input_format == "docx":
                output_path = await self._convert_docx(input_path, output_format)
            elif input_format == "image":
                output_path = await self._convert_image(input_path, output_format)
            elif input_format == "url":
                output_path = await self._convert_url(input_path, output_format)
            elif input_format == "html":
                output_path = await self._convert_html(input_path, output_format)
            else:
                raise ValueError(f"Unsupported input format: {input_format}")

            task_manager.update_task(
                self.task.task_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                message="Conversion completed",
                download_filename=output_path.name,
            )
            return output_path

        except Exception as e:
            task_manager.update_task(
                self.task.task_id,
                status=TaskStatus.FAILED,
                message=f"Conversion failed: {str(e)}",
            )
            raise

    async def _convert_epub(self, input_path: Path, output_format: str) -> Path:
        """Convert EPUB to target format."""
        self.update_progress(20, "Reading EPUB file...")

        if output_format == "md_single":
            from epub_to_md import convert_epub_to_single_markdown

            output_path = self.file_manager.get_output_path(
                f"{input_path.stem}.md"
            )
            assets_dir = self.file_manager.output_dir / "assets"
            self.update_progress(50, "Converting to Markdown...")
            convert_epub_to_single_markdown(
                input_path,
                output_file=output_path,
                assets_dir=assets_dir,
            )
            return output_path

        elif output_format == "md_chapters":
            from epub_to_md import convert_epub_to_markdown

            self.update_progress(50, "Converting to Markdown chapters...")
            chapters = convert_epub_to_markdown(
                input_path,
                output_dir=self.file_manager.output_dir / "chapters",
                assets_dir=self.file_manager.output_dir / "assets",
            )
            # Create ZIP of chapters
            zip_path = self.file_manager.get_output_path(f"{input_path.stem}.zip")
            self.update_progress(80, "Creating ZIP archive...")
            with zipfile.ZipFile(zip_path, "w") as zf:
                for chapter in chapters:
                    if chapter.output_path and chapter.output_path.exists():
                        zf.write(chapter.output_path, chapter.output_path.name)
                # Add assets
                assets_dir = self.file_manager.output_dir / "assets"
                if assets_dir.exists():
                    for asset in assets_dir.iterdir():
                        zf.write(asset, f"assets/{asset.name}")
            return zip_path

        elif output_format == "html":
            from epub_to_html import convert_epub

            self.update_progress(50, "Converting to HTML...")
            html_path = convert_epub(input_path, self.file_manager.output_dir)
            return html_path

        elif output_format == "pdf":
            from epub_to_html import convert_epub
            from html_to_pdf import convert_html_to_pdf

            self.update_progress(30, "Converting to HTML...")
            html_path = convert_epub(input_path, self.file_manager.output_dir)
            self.update_progress(60, "Converting to PDF...")
            pdf_path = convert_html_to_pdf(
                html_path,
                output_path=self.file_manager.get_output_path(f"{input_path.stem}.pdf"),
            )
            return pdf_path

        raise ValueError(f"Unsupported output format for EPUB: {output_format}")

    async def _convert_pdf(self, input_path: Path, output_format: str) -> Path:
        """Convert PDF to target format."""
        self.update_progress(20, "Analyzing PDF...")

        # Import PDF converter (handles both text and scanned PDFs)
        from pdf_to_md import convert_pdf_to_markdown

        md_path = await convert_pdf_to_markdown(
            input_path,
            self.file_manager.output_dir,
            progress_callback=lambda p, m: self.update_progress(20 + int(p * 0.5), m),
        )

        if output_format == "md_single":
            return md_path

        elif output_format == "html":
            self.update_progress(80, "Converting to HTML...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.file_manager.get_output_path(f"{input_path.stem}.html")
            html_path.write_text(html_content, encoding="utf-8")
            return html_path

        elif output_format == "pdf":
            # For PDF input, if it's a scanned PDF we OCR'd, regenerate PDF from HTML
            self.update_progress(80, "Generating PDF...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.file_manager.get_output_path(f"{input_path.stem}_temp.html")
            html_path.write_text(html_content, encoding="utf-8")

            from html_to_pdf import convert_html_to_pdf

            pdf_path = convert_html_to_pdf(
                html_path,
                output_path=self.file_manager.get_output_path(
                    f"{input_path.stem}_converted.pdf"
                ),
            )
            return pdf_path

        raise ValueError(f"Unsupported output format for PDF: {output_format}")

    async def _convert_docx(self, input_path: Path, output_format: str) -> Path:
        """Convert Word document to target format."""
        self.update_progress(20, "Reading Word document...")

        from docx_to_md import convert_docx_to_markdown

        md_path = convert_docx_to_markdown(input_path, self.file_manager.output_dir)
        self.update_progress(60, "Converted to Markdown")

        if output_format == "md_single":
            return md_path

        elif output_format == "html":
            self.update_progress(80, "Converting to HTML...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.file_manager.get_output_path(f"{input_path.stem}.html")
            html_path.write_text(html_content, encoding="utf-8")
            return html_path

        elif output_format == "pdf":
            self.update_progress(80, "Converting to PDF...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.file_manager.get_output_path(f"{input_path.stem}_temp.html")
            html_path.write_text(html_content, encoding="utf-8")

            from html_to_pdf import convert_html_to_pdf

            pdf_path = convert_html_to_pdf(
                html_path,
                output_path=self.file_manager.get_output_path(f"{input_path.stem}.pdf"),
            )
            return pdf_path

        raise ValueError(f"Unsupported output format for DOCX: {output_format}")

    async def _convert_image(self, input_path: Path, output_format: str) -> Path:
        """Convert image to target format using OCR."""
        self.update_progress(20, "Processing image with OCR...")

        from ocr_to_md import convert_with_ocr

        md_path, _ = await convert_with_ocr(
            input_path,
            self.file_manager.output_dir,
        )
        self.update_progress(70, "OCR completed")

        if output_format == "md_single":
            return md_path

        elif output_format == "html":
            self.update_progress(80, "Converting to HTML...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.file_manager.get_output_path(f"{input_path.stem}.html")
            html_path.write_text(html_content, encoding="utf-8")
            return html_path

        elif output_format == "pdf":
            self.update_progress(80, "Converting to PDF...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.file_manager.get_output_path(f"{input_path.stem}_temp.html")
            html_path.write_text(html_content, encoding="utf-8")

            from html_to_pdf import convert_html_to_pdf

            pdf_path = convert_html_to_pdf(
                html_path,
                output_path=self.file_manager.get_output_path(f"{input_path.stem}.pdf"),
            )
            return pdf_path

        raise ValueError(f"Unsupported output format for image: {output_format}")

    async def _convert_url(self, input_path: Path, output_format: str) -> Path:
        """Convert URL content to target format."""
        # input_path contains the URL as text
        url = input_path.read_text(encoding="utf-8").strip()
        self.update_progress(20, "Fetching URL content...")

        from url_to_md import fetch_url_to_markdown

        md_content, title = await fetch_url_to_markdown(url)
        filename = title or "webpage"
        md_path = self.file_manager.get_output_path(f"{filename}.md")
        md_path.write_text(md_content, encoding="utf-8")
        self.update_progress(60, "Content extracted")

        if output_format == "md_single":
            return md_path

        elif output_format == "html":
            self.update_progress(80, "Converting to HTML...")
            html_content = self._markdown_to_html(md_content)
            html_path = self.file_manager.get_output_path(f"{filename}.html")
            html_path.write_text(html_content, encoding="utf-8")
            return html_path

        elif output_format == "pdf":
            self.update_progress(80, "Converting to PDF...")
            html_content = self._markdown_to_html(md_content)
            html_path = self.file_manager.get_output_path(f"{filename}_temp.html")
            html_path.write_text(html_content, encoding="utf-8")

            from html_to_pdf import convert_html_to_pdf

            pdf_path = convert_html_to_pdf(
                html_path,
                output_path=self.file_manager.get_output_path(f"{filename}.pdf"),
            )
            return pdf_path

        raise ValueError(f"Unsupported output format for URL: {output_format}")

    async def _convert_html(self, input_path: Path, output_format: str) -> Path:
        """Convert HTML to target format."""
        self.update_progress(20, "Reading HTML file...")

        if output_format == "md_single":
            from html_to_md import convert_html_to_markdown_single

            self.update_progress(50, "Converting to Markdown...")
            doc = convert_html_to_markdown_single(
                input_path, output_root=self.file_manager.output_dir
            )
            return doc.output_path

        elif output_format == "pdf":
            self.update_progress(50, "Converting to PDF...")
            from html_to_pdf import convert_html_to_pdf

            pdf_path = convert_html_to_pdf(
                input_path,
                output_path=self.file_manager.get_output_path(f"{input_path.stem}.pdf"),
            )
            return pdf_path

        raise ValueError(f"Unsupported output format for HTML: {output_format}")

    def _markdown_to_html(self, markdown_content: str) -> str:
        """Convert Markdown to HTML with basic styling."""
        # Use markdownify in reverse - actually we need a markdown parser
        # For now, use a simple approach with the markdown library if available
        try:
            import markdown

            html_body = markdown.markdown(
                markdown_content,
                extensions=["tables", "fenced_code"],
            )
        except ImportError:
            # Fallback: wrap in pre tag
            html_body = f"<pre>{markdown_content}</pre>"

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
        pre {{ background: #f4f4f4; padding: 10px; overflow-x: auto; }}
        code {{ background: #f4f4f4; padding: 2px 5px; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f4f4f4; }}
        img {{ max-width: 100%; }}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""
