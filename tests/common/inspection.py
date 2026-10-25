"""Inspect published artifacts against independently authored source markers.

This is intentionally not a converter. It reads output, verifies content order
and local dependencies, and leaves visual/OCR judgement explicitly pending.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes, urlsplit

import markdown
from bs4 import BeautifulSoup
from markdown.extensions.toc import slugify_unicode
from PIL import Image


def _soup(path: Path) -> BeautifulSoup:
    source = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".md":
        # Front matter is generated metadata, not source body.
        source = re.sub(r"\A---\n.*?\n---\n", "", source, count=1, flags=re.DOTALL)
        source = markdown.markdown(
            source, extensions=["tables", "fenced_code", "toc"],
            extension_configs={"toc": {"slugify": slugify_unicode}},
        )
    return BeautifulSoup(source, "html.parser")


def _references(soup: BeautifulSoup) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    for element in soup.find_all(True):
        for attribute in ("href", "src", "poster"):
            value = element.get(attribute)
            if isinstance(value, str):
                references.append((attribute, value))
        srcset = element.get("srcset")
        if isinstance(srcset, str) and not srcset.startswith("data:"):
            references.extend(("srcset", item.strip().split()[0]) for item in srcset.split(",") if item.strip())
        css = str(element.get("style") or "")
        if element.name == "style":
            css += element.get_text()
        references.extend(("css", match.group(1).strip(" '\"")) for match in re.finditer(r"url\(([^)]+)\)", css))
    return references


def image_identity(data: bytes) -> str:
    """Use decoded pixels, so a renamed/invalid file cannot satisfy the oracle."""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        rgb = image.convert("RGB")
        return hashlib.sha256(f"{rgb.width}x{rgb.height}:RGB:".encode() + rgb.tobytes()).hexdigest()


def _target_marker(soup: BeautifulSoup, fragment: str, markers: list[str]) -> str | None:
    anchor = (soup.find(id=fragment) or soup.find(attrs={"name": fragment})) if fragment else soup
    if anchor is None:
        return None
    descendants = list(soup.descendants)
    start = next((index for index, element in enumerate(descendants) if element is anchor), -1)
    # Markers are unique source-body strings, never generated navigation labels.
    text = " ".join(str(element) for element in descendants[start + 1:] if isinstance(element, str))
    found = [(text.find(marker), marker) for marker in markers if marker in text]
    return min(found)[1] if found else None


def _cells(soup: BeautifulSoup) -> list[dict]:
    cells = []
    for table in soup.find_all("table"):
        occupied: set[tuple[int, int]] = set()
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        for row_index, row in enumerate(rows):
            column = 0
            for cell in row.find_all(["td", "th"], recursive=False):
                while (row_index, column) in occupied:
                    column += 1
                rowspan, colspan = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
                if not 1 <= rowspan <= 1000 or not 1 <= colspan <= 1000:
                    raise ValueError("Invalid table span in output")
                cells.append({"text": cell.get_text(" "), "row": row_index, "column": column, "rowspan": rowspan, "colspan": colspan})
                occupied.update((r, c) for r in range(row_index, row_index + rowspan) for c in range(column, column + colspan))
                column += colspan
    return cells


def inspect_output(paths: list[Path], case: dict, root: Path) -> dict:
    """Check one view (whole OR split), never count duplicated export views."""
    if not paths:
        return {"passed": False, "failures": ["No readable primary artifact"], "visual_status": "not_run"}
    root = root.resolve()
    documents = [(path, _soup(path)) for path in paths]
    text_parts = []
    for _, soup in documents:
        clone = BeautifulSoup(str(soup), "html.parser")
        for element in clone.find_all(["script", "style", "head"]):
            element.decompose()
        text_parts.append(clone.get_text(" "))
    text = "\n".join(text_parts)
    failures: list[str] = []
    markers = case["markers"]
    counts = {marker: text.count(marker) for marker in markers}
    for marker, count in counts.items():
        if count != 1:
            failures.append(f"{marker}: expected once, found {count}")
    present = [text.find(marker) for marker in markers if counts[marker] == 1]
    if present != sorted(present):
        failures.append("Source body order changed")

    broken: list[str] = []
    local_checks = 0
    external: list[str] = []
    image_files: set[Path] = set()
    image_pixels: set[str] = set()
    parsed = {path.resolve(): soup for path, soup in documents}
    for path, soup in documents:
        for attribute, value in _references(soup):
            url = urlsplit(value)
            if url.scheme or url.netloc:
                if url.scheme == "data" and attribute in {"src", "srcset", "poster"}:
                    try:
                        header, payload = value.split(",", 1)
                        data = base64.b64decode(payload, validate=True) if ";base64" in header else unquote_to_bytes(payload)
                        image_pixels.add(image_identity(data))
                    except (ValueError, OSError) as error:
                        broken.append(f"{path.name}: invalid inline image: {type(error).__name__}")
                if url.scheme != "data":
                    external.append(value)
                continue
            if not url.path and not url.fragment:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path.resolve()
            local_checks += 1
            if not target.is_relative_to(root):
                broken.append(f"{path.name}: dependency outside export: {value}")
                continue
            if not target.is_file():
                broken.append(f"{path.name}: missing dependency: {value}")
                continue
            if attribute in {"src", "srcset", "poster"}:
                image_files.add(target)
                try:
                    image_pixels.add(image_identity(target.read_bytes()))
                except (ValueError, OSError) as error:
                    broken.append(f"{path.name}: invalid image {value}: {type(error).__name__}")
            if url.fragment and target.suffix.lower() in {".html", ".htm", ".md"}:
                if target not in parsed:
                    parsed[target] = _soup(target)
                fragment = unquote(url.fragment)
                if not (parsed[target].find(id=fragment) or parsed[target].find(attrs={"name": fragment})):
                    broken.append(f"{path.name}: missing anchor: {value}")
    failures.extend(broken)
    if len(image_pixels) < case.get("minimum_images", 0):
        failures.append(f"Expected at least {case['minimum_images']} distinct decoded images, found {len(image_pixels)}")
    for identity in case.get("image_pixels", []):
        if identity not in image_pixels:
            failures.append(f"Source image pixels lost: {identity}")
    for attribute in case.get("required_spans", []):
        if not any(soup.find(attrs={attribute: True}) for _, soup in documents):
            failures.append(f"Table {attribute} was lost")
    cells = [cell for _, soup in documents for cell in _cells(soup)]
    for expected in case.get("required_cells", []):
        matches = [cell for cell in cells if expected["marker"] in cell["text"]]
        if not any(all(cell.get(key) == value for key, value in expected.items() if key != "marker") for cell in matches):
            failures.append(f"Table cell structure changed: {expected['marker']}")
    for expected in case.get("required_links", []):
        matched = False
        for path, soup in documents:
            for link in soup.find_all("a", href=True):
                if link.get_text(" ", strip=True) != expected["label"]:
                    continue
                url = urlsplit(str(link["href"]))
                if url.scheme or url.netloc:
                    continue
                target = (path.parent / unquote(url.path)).resolve() if url.path else path.resolve()
                if target in parsed and _target_marker(parsed[target], unquote(url.fragment), markers) == expected["target_marker"]:
                    matched = True
        if not matched:
            failures.append(f"Source link lost or mistargeted: {expected['label']} -> {expected['target_marker']}")
    code = case.get("code_contains")
    if code and not any(code in element.get_text() for _, soup in documents for element in soup.find_all(["pre", "code"])):
        failures.append("Code content or indentation was lost")
    for title in case.get("required_titles", []):
        if title not in text:
            failures.append(f"Required section title lost: {title}")
    return {
        "passed": not failures,
        "automatic_scope": "source marker order, local/required links, decoded image identities, required table cells and code",
        "overall_status": "unverified; visual/source review still required",
        "marker_counts": counts,
        "local_references_checked": local_checks,
        "local_images": len(image_files),
        "decoded_images": len(image_pixels),
        "external_references": sorted(set(external)),
        "failures": failures,
        "visual_status": "not_run",
        "visual_checks": case.get("visual_checks", []),
        "ocr_status": "not_run" if case.get("requires_ocr") else "not_applicable",
    }


def inspect_pdf(path: Path, case: dict) -> dict:
    """Text/embedding checks do not claim visual or OCR quality validation."""
    import pymupdf

    with pymupdf.open(path) as document:
        text = "\n".join(page.get_text() for page in document)
        pages = len(document)
        image_pixels = {
            image_identity(document.extract_image(xref)["image"])
            for xref in {item[0] for page in document for item in page.get_images()}
        }
        images = len(image_pixels)
    counts = {marker: text.count(marker) for marker in case["markers"]}
    failures = [f"{marker}: expected once, found {count}" for marker, count in counts.items() if count != 1]
    positions = [text.find(marker) for marker, count in counts.items() if count == 1]
    if positions != sorted(positions):
        failures.append("Source body order changed")
    if images < case.get("minimum_images", 0):
        failures.append(f"Expected at least {case['minimum_images']} embedded images, found {images}")
    for identity in case.get("image_pixels", []):
        if identity not in image_pixels:
            failures.append(f"Source image pixels lost: {identity}")
    return {
        "passed": not failures,
        "automatic_scope": "source marker order and embedded image identities only; PDF table/link/layout checks are manual",
        "overall_status": "unverified; visual/source review still required",
        "marker_counts": counts,
        "output_pages": pages,
        "embedded_images": images,
        "failures": failures,
        "visual_status": "not_run",
        "visual_checks": case.get("visual_checks", []),
        "ocr_status": "not_run" if case.get("requires_ocr") else "not_applicable",
    }
