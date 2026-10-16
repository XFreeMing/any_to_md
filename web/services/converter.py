"""Web boundary for the shared runtime and formats awaiting Reader cutover."""

import asyncio
from pathlib import Path

from any_to_md.application.convert import ConversionRequest, convert
from any_to_md.application.capabilities import CAPABILITIES
from any_to_md.application.jobs import JobExecution, JobRuntime
from any_to_md.domain.jobs import Job
from any_to_md.export.bundle import finalize_bundle


class ConversionService:
    def __init__(self, job: Job, runtime: JobRuntime):
        self.job = job
        self.runtime = runtime
        self.execution: JobExecution | None = None

    def update_progress(self, progress: int, message: str):
        self.execution.progress(progress, message)

    def output_path(self, filename: str) -> Path:
        return self.runtime.storage.output_path(self.execution.root, filename)

    async def convert(self, output_format: str) -> Path:
        if output_format != self.job.output_format:
            raise ValueError('输出配置与持久任务不一致')
        if CAPABILITIES[self.job.input_format].reader is not None:
            await asyncio.to_thread(convert, ConversionRequest(data_dir=self.runtime.storage.root, job_id=self.job.job_id))
            return await asyncio.to_thread(self.runtime.download, self.job.job_id)
        # These formats still have their sole existing parsers. State, files,
        # packaging and publication already use the same runtime as EPUB/CLI.
        with self.runtime.execute(self.job.job_id) as execution:
            self.execution = execution
            execution.begin()
            input_path = self.runtime.source_path(self.job)
            handlers = {'pdf': self._convert_pdf, 'image': self._convert_image}
            handler = handlers.get(self.job.input_format)
            if handler is None:
                raise ValueError(f'Unsupported input format: {self.job.input_format}')
            output_path = await handler(input_path, output_format)
            relative = output_path.relative_to(execution.root).as_posix()
            report = {'schema_version': 2, 'job_id': self.job.job_id, 'export_id': execution.export_id, 'status': 'completed', 'targets': self.job.targets, 'diagnostics': [], 'limitations': ['输出完整性与布局需按相应格式的质量记录评估。']}
            artifacts = finalize_bundle(execution.root, report, self.job.targets, execution.export_id, views={output_format: relative})
            await asyncio.to_thread(execution.publish, report, artifacts)
            return await asyncio.to_thread(self.runtime.download, self.job.job_id)

    async def _convert_pdf(self, input_path: Path, output_format: str) -> Path:
        """Convert PDF to target format."""
        self.update_progress(20, "Analyzing PDF...")

        # Import PDF converter (handles both text and scanned PDFs)
        from pdf_to_md import convert_pdf_to_markdown

        md_path = await convert_pdf_to_markdown(
            input_path,
            self.execution.root,
            progress_callback=lambda p, m: self.update_progress(20 + int(p * 0.5), m),
        )

        if output_format == "md_single":
            return md_path

        elif output_format == "html":
            self.update_progress(80, "Converting to HTML...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.output_path(f"{input_path.stem}.html")
            html_path.write_text(html_content, encoding="utf-8")
            return html_path

        elif output_format == "pdf":
            # For PDF input, if it's a scanned PDF we OCR'd, regenerate PDF from HTML
            self.update_progress(80, "Generating PDF...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.output_path(f"{input_path.stem}_temp.html")
            html_path.write_text(html_content, encoding="utf-8")

            from any_to_md.writers.pdf import render_pdf

            pdf_path = render_pdf(
                html_path,
                output_path=self.output_path(
                    f"{input_path.stem}_converted.pdf"
                ),
            )
            return pdf_path

        raise ValueError(f"Unsupported output format for PDF: {output_format}")

    async def _convert_image(self, input_path: Path, output_format: str) -> Path:
        """Convert image to target format using OCR."""
        self.update_progress(20, "Processing image with OCR...")

        from ocr_to_md import convert_with_ocr

        md_path, _ = await convert_with_ocr(
            input_path,
            self.execution.root,
        )
        self.update_progress(70, "OCR completed")

        if output_format == "md_single":
            return md_path

        elif output_format == "html":
            self.update_progress(80, "Converting to HTML...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.output_path(f"{input_path.stem}.html")
            html_path.write_text(html_content, encoding="utf-8")
            return html_path

        elif output_format == "pdf":
            self.update_progress(80, "Converting to PDF...")
            html_content = self._markdown_to_html(md_path.read_text(encoding="utf-8"))
            html_path = self.output_path(f"{input_path.stem}_temp.html")
            html_path.write_text(html_content, encoding="utf-8")

            from any_to_md.writers.pdf import render_pdf

            pdf_path = render_pdf(
                html_path,
                output_path=self.output_path(f"{input_path.stem}.pdf"),
            )
            return pdf_path

        raise ValueError(f"Unsupported output format for image: {output_format}")

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
