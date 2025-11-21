"""Minimal EPUB→HTML batch converter.

The script scans the directory that hosts this file for every ``*.epub`` file
and emits a single HTML document for each one under ``output/`` (next to this
script). The resulting HTML simply concatenates every XHTML resource listed in
the EPUB spine so the reading order mirrors the source book.
"""

from __future__ import annotations

import logging
import posixpath
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)


@dataclass
class ManifestItem:
    item_id: str
    href: str
    path: str
    media_type: str


def _normalize_epub_path(path: str) -> str:
    sanitized = (path or "").replace("\\", "/")
    normalized = posixpath.normpath(sanitized)
    if normalized in ("", "."):
        return ""
    return normalized


def _resolve_relative_path(current_doc_path: str, target: str) -> str:
    reference = (target or "").strip()
    if not reference:
        return ""
    reference = reference.replace("\\", "/")
    if reference.startswith("/"):
        return _normalize_epub_path(reference.lstrip("/"))
    base_dir = posixpath.dirname(current_doc_path)
    combined = posixpath.join(base_dir, reference)
    return _normalize_epub_path(combined)


def _slugify_path(path: str, index: int) -> str:
    stem = posixpath.splitext(posixpath.basename(path))[0]
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", stem).strip("-") or "section"
    return f"s{index:04d}-{slug}"


def _setup_logging() -> None:
    if LOGGER.handlers:
        return
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


def _locate_rootfile(epub: zipfile.ZipFile) -> str:
    try:
        container_xml = epub.read("META-INF/container.xml")
    except KeyError as exc:
        raise ValueError("Missing META-INF/container.xml in EPUB") from exc

    tree = ET.fromstring(container_xml)
    for rootfile in tree.findall(".//{*}rootfile"):
        full_path = rootfile.attrib.get("full-path")
        if full_path:
            return full_path
    raise ValueError("Could not determine package document (OPF) location")


def _extract_namespaces(root: ET.Element) -> Dict[str, str]:
    ns: Dict[str, str] = {"dc": "http://purl.org/dc/elements/1.1/"}
    if root.tag.startswith("{") and "}" in root.tag:
        ns["opf"] = root.tag[1 : root.tag.index("}")]
    else:
        ns["opf"] = ""
    return ns


def _parse_package_document(
    epub: zipfile.ZipFile, opf_path: str
) -> Tuple[Dict[str, ManifestItem], List[str], Dict[str, str]]:
    try:
        opf_xml = epub.read(opf_path)
    except KeyError as exc:
        raise ValueError(f"Package document {opf_path} is missing") from exc

    root = ET.fromstring(opf_xml)
    ns = _extract_namespaces(root)

    manifest: Dict[str, ManifestItem] = {}
    opf_dir = _normalize_epub_path(posixpath.dirname(opf_path))
    for item in root.findall(".//opf:manifest/opf:item", ns):
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        media_type = item.attrib.get("media-type", "")
        if not item_id or not href:
            continue
        base = opf_dir if opf_dir else ""
        raw_path = posixpath.join(base, href) if base else href
        full_path = _normalize_epub_path(raw_path)
        manifest[item_id] = ManifestItem(
            item_id=item_id,
            href=href,
            path=full_path,
            media_type=media_type,
        )

    spine: List[str] = []
    for itemref in root.findall(".//opf:spine/opf:itemref", ns):
        idref = itemref.attrib.get("idref")
        if idref:
            spine.append(idref)

    metadata: Dict[str, str] = {}
    title = root.findtext(".//dc:title", namespaces=ns)
    if title:
        metadata["title"] = title.strip()
    return manifest, spine, metadata


def _extract_asset(
    epub: zipfile.ZipFile,
    resource_path: str,
    assets_dir: Path,
    cache: Dict[str, str],
) -> str | None:
    normalized = _normalize_epub_path(resource_path)
    if not normalized or normalized.startswith(".."):
        return None
    if normalized in cache:
        return cache[normalized]
    try:
        payload = epub.read(normalized)
    except KeyError:
        LOGGER.warning("Missing asset '%s'", normalized)
        return None
    destination = assets_dir / normalized
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    relative_target = posixpath.join(assets_dir.name, normalized)
    cache[normalized] = relative_target
    return relative_target


def _remap_resource_url(
    original: str | None,
    current_doc_path: str,
    epub: zipfile.ZipFile,
    assets_dir: Path,
    cache: Dict[str, str],
) -> str | None:
    if not original:
        return None
    parsed = urlparse(original)
    if parsed.scheme and parsed.scheme not in ("", "data"):
        return None
    if parsed.scheme == "data" or parsed.netloc:
        return None
    normalized = _resolve_relative_path(current_doc_path, parsed.path)
    if not normalized:
        return None
    asset_href = _extract_asset(epub, normalized, assets_dir, cache)
    if not asset_href:
        return None
    rebuilt = asset_href
    if parsed.fragment:
        rebuilt += f"#{parsed.fragment}"
    if parsed.query:
        rebuilt += f"?{parsed.query}"
    return rebuilt


def _rewrite_ids_and_names(soup: BeautifulSoup, prefix: str) -> None:
    for tag in soup.find_all(True):
        existing_id = tag.get("id")
        if existing_id:
            tag["id"] = f"{prefix}-{existing_id}"
        if tag.name == "a":
            anchor_name = tag.get("name")
            if anchor_name:
                tag["name"] = f"{prefix}-{anchor_name}"


def _rewrite_images(
    soup: BeautifulSoup,
    current_doc_path: str,
    epub: zipfile.ZipFile,
    assets_dir: Path,
    cache: Dict[str, str],
) -> None:
    for img in soup.find_all("img"):
        new_src = _remap_resource_url(
            img.get("src"), current_doc_path, epub, assets_dir, cache
        )
        if new_src:
            img["src"] = new_src
        srcset = img.get("srcset")
        if not srcset:
            continue
        rewritten_entries: List[str] = []
        for candidate in srcset.split(","):
            chunk = candidate.strip()
            if not chunk:
                continue
            parts = chunk.split()
            url_part = parts[0]
            descriptor = " ".join(parts[1:])
            updated = _remap_resource_url(
                url_part, current_doc_path, epub, assets_dir, cache
            )
            if not updated:
                continue
            rewritten_entries.append(f"{updated} {descriptor}".strip())
        if rewritten_entries:
            img["srcset"] = ", ".join(rewritten_entries)


def _rewrite_anchor_tags(
    soup: BeautifulSoup,
    current_doc_path: str,
    prefix: str,
    doc_prefixes: Dict[str, str],
    local_ids: Set[str],
    global_ids: Dict[str, str],
    epub: zipfile.ZipFile,
    assets_dir: Path,
    cache: Dict[str, str],
) -> None:
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href:
            continue
        lowered = href.lower()
        if lowered.startswith("mailto:") or lowered.startswith("javascript:"):
            continue
        if href.startswith("#"):
            fragment = href[1:]
            if not fragment:
                anchor["href"] = f"#{prefix}"
                continue
            if fragment in local_ids:
                anchor["href"] = f"#{prefix}-{fragment}"
                continue
            dest_prefix = global_ids.get(fragment)
            if dest_prefix:
                anchor["href"] = f"#{dest_prefix}-{fragment}"
                continue
            anchor["href"] = f"#{prefix}-{fragment}"
            continue
        parsed = urlparse(href)
        if parsed.scheme and parsed.scheme not in ("", "data"):
            continue
        if parsed.netloc:
            continue
        target_path = _resolve_relative_path(current_doc_path, parsed.path)
        if target_path in doc_prefixes:
            dest_prefix = doc_prefixes[target_path]
            anchor["href"] = (
                f"#{dest_prefix}-{parsed.fragment}"
                if parsed.fragment
                else f"#{dest_prefix}"
            )
            continue
        # treat as binary/linkable asset
        rebuilt = _remap_resource_url(href, current_doc_path, epub, assets_dir, cache)
        if rebuilt:
            anchor["href"] = rebuilt


def _transform_spine_document(
    raw_html: str,
    doc_path: str,
    prefix: str,
    doc_prefixes: Dict[str, str],
    local_ids: Set[str],
    global_ids: Dict[str, str],
    epub: zipfile.ZipFile,
    assets_dir: Path,
    cache: Dict[str, str],
) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    _rewrite_ids_and_names(soup, prefix)
    _rewrite_images(soup, doc_path, epub, assets_dir, cache)
    _rewrite_anchor_tags(
        soup,
        doc_path,
        prefix,
        doc_prefixes,
        local_ids,
        global_ids,
        epub,
        assets_dir,
        cache,
    )
    body = soup.body
    inner_html = body.decode_contents() if body else soup.decode()
    return f'<section id="{prefix}" data-source="{doc_path}">\n{inner_html}\n</section>'


def _render_spine_sections(
    epub: zipfile.ZipFile,
    manifest: Dict[str, ManifestItem],
    spine: Iterable[str],
    assets_dir: Path,
) -> List[str]:
    doc_entries: List[Tuple[ManifestItem, str]] = []
    doc_prefixes: Dict[str, str] = {}
    doc_ids: Dict[str, Set[str]] = {}
    raw_cache: Dict[str, str] = {}
    for index, idref in enumerate(spine):
        item = manifest.get(idref)
        if not item or not item.path:
            LOGGER.warning("Skipping spine item '%s' with no manifest entry", idref)
            continue
        prefix = _slugify_path(item.path, index)
        doc_entries.append((item, prefix))
        doc_prefixes[item.path] = prefix

    global_ids: Dict[str, str] = {}
    for manifest_item, prefix in doc_entries:
        try:
            raw_bytes = epub.read(manifest_item.path)
        except KeyError:
            LOGGER.warning(
                "Missing resource '%s' referenced by id '%s'",
                manifest_item.path,
                manifest_item.item_id,
            )
            continue
        raw_html = raw_bytes.decode("utf-8", errors="ignore")
        raw_cache[manifest_item.path] = raw_html
        soup = BeautifulSoup(raw_html, "html.parser")
        ids: Set[str] = set()
        for tag in soup.find_all(True):
            tag_id = tag.get("id")
            if tag_id:
                ids.add(tag_id)
            if tag.name == "a":
                name_attr = tag.get("name")
                if name_attr:
                    ids.add(name_attr)
        doc_ids[manifest_item.path] = ids
        for fragment in ids:
            global_ids.setdefault(fragment, prefix)

    asset_cache: Dict[str, str] = {}
    sections: List[str] = []
    for manifest_item, prefix in doc_entries:
        raw_html = raw_cache.get(manifest_item.path)
        if raw_html is None:
            continue
        sections.append(
            _transform_spine_document(
                raw_html,
                manifest_item.path,
                prefix,
                doc_prefixes,
                doc_ids.get(manifest_item.path, set()),
                global_ids,
                epub,
                assets_dir,
                asset_cache,
            )
        )
    return sections


def _build_html_document(pages: Iterable[str], title: str) -> str:
    joined = "\n\n".join(pages)
    safe_title = title or "Untitled EPUB"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8" />\n'
        f"  <title>{safe_title}</title>\n"
        "</head>\n"
        "<body>\n"
        f"{joined}\n"
        "</body>\n"
        "</html>\n"
    )


def convert_epub(epub_path: Path, output_dir: Path) -> Path:
    LOGGER.info("Converting %s", epub_path.name)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / f"{epub_path.stem}_assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub_path) as epub:
        opf_path = _locate_rootfile(epub)
        manifest, spine, meta = _parse_package_document(epub, opf_path)
        pages = _render_spine_sections(epub, manifest, spine, assets_dir)

    if not pages:
        raise RuntimeError(f"No XHTML documents found for {epub_path.name}")

    html = _build_html_document(pages, meta.get("title", epub_path.stem))
    target = output_dir / f"{epub_path.stem}.html"
    target.write_text(html, encoding="utf-8")
    LOGGER.info("Saved %s", target)
    return target


def gather_epub_files(folder: Path) -> List[Path]:
    return sorted(p for p in folder.glob("*.epub") if p.is_file())


def main() -> None:
    _setup_logging()
    script_dir = Path(__file__).resolve().parent
    output_dir = script_dir / "output"
    epub_files = gather_epub_files(script_dir)

    if not epub_files:
        LOGGER.warning("No EPUB files found in %s", script_dir)
        return

    for epub_file in epub_files:
        try:
            convert_epub(epub_file, output_dir)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.error("Failed to convert %s: %s", epub_file.name, exc)


if __name__ == "__main__":
    main()
