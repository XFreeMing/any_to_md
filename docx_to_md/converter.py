"""Word document to Markdown conversion using mammoth."""

from pathlib import Path

import mammoth
from markdownify import markdownify as md


def convert_docx_to_markdown(
    docx_path: Path | str,
    output_dir: Path | str,
    extract_images: bool = True,
) -> Path:
    """
    Convert Word document to Markdown.

    Args:
        docx_path: Path to the Word document
        output_dir: Directory for output files
        extract_images: Whether to extract images from the document

    Returns:
        Path to the generated Markdown file
    """
    docx_path = Path(docx_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    images_dir = output_dir / "images"
    image_counter = [0]  # Use list for mutable counter in closure

    def convert_image(image):
        """Handle embedded images."""
        if not extract_images:
            return {}

        images_dir.mkdir(exist_ok=True)
        image_counter[0] += 1
        extension = image.content_type.split("/")[-1] if image.content_type else "png"
        filename = f"image_{image_counter[0]}.{extension}"
        image_path = images_dir / filename

        with image.open() as image_bytes:
            image_path.write_bytes(image_bytes.read())

        return {"src": f"images/{filename}"}

    # Convert DOCX to HTML using mammoth
    with open(docx_path, "rb") as docx_file:
        result = mammoth.convert_to_html(
            docx_file,
            convert_image=mammoth.images.img_element(convert_image),
        )
        html_content = result.value

    # Convert HTML to Markdown
    md_content = md(
        html_content,
        heading_style="ATX",
        bullets="-",
        code_language="",
    )

    # Clean up the markdown
    md_content = _clean_markdown(md_content)

    # Write output
    md_path = output_dir / f"{docx_path.stem}.md"
    md_path.write_text(md_content, encoding="utf-8")

    return md_path


def _clean_markdown(content: str) -> str:
    """Clean up markdown content."""
    lines = content.split("\n")
    cleaned_lines = []
    prev_empty = False

    for line in lines:
        # Remove trailing whitespace
        line = line.rstrip()

        # Collapse multiple empty lines
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue

        cleaned_lines.append(line)
        prev_empty = is_empty

    return "\n".join(cleaned_lines).strip() + "\n"
