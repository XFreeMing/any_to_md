"""Private real-EPUB comparison using source paragraph probes, not snapshots.

Probe text and user-book output stay in the ignored output directory. This
partial, independently sampled comparison is not a full-book quality verdict.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import posixpath
import sys
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import markdown
from bs4 import BeautifulSoup

from tests.quality.capture import _SourceTreeFinder, _html_pipeline, _module_origins, _new_book, _old_md


def _normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


class _Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[list[str]] = []
        self.values: list[str] = []
        self.body_text: list[str] = []
        self.in_body = False
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.in_body and not self.ignored and tag in {"p", "pre"}:
            self.stack.append([])

    def handle_endtag(self, tag):
        if tag == "body":
            self.in_body = False
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if tag in {"p", "pre"} and self.stack:
            self.values.append("".join(self.stack.pop()))

    def handle_data(self, data):
        if not self.in_body or self.ignored:
            return
        self.body_text.append(data)
        for parts in self.stack:
            parts.append(data)


def source_probes(source: Path, maximum: int = 64) -> tuple[list[str], dict]:
    """Read OPF/spine independently of ebooklib and converter chapter rules."""
    with ZipFile(source) as archive:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None:
            raise ValueError("EPUB has no rootfile")
        package_path = rootfile.attrib["full-path"]
        package = ElementTree.fromstring(archive.read(package_path))
        manifest = {item.attrib["id"]: item.attrib for item in package.findall("./{*}manifest/{*}item")}
        paragraphs = []
        source_text = []
        documents = 0
        for ref in package.findall("./{*}spine/{*}itemref"):
            item = manifest[ref.attrib["idref"]]
            if "nav" in item.get("properties", "").split():
                continue
            member = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), item["href"]))
            parser = _Paragraphs()
            parser.feed(archive.read(member).decode("utf-8"))
            paragraphs.extend(_normalize(value) for value in parser.values)
            source_text.append(_normalize("".join(parser.body_text)))
            documents += 1
    counts = collections.Counter(paragraphs)
    candidates = [text for text in paragraphs if 40 <= len(text) <= 240 and counts[text] == 1]
    if not candidates:
        raise ValueError("No suitable unique source paragraphs; choose another probe strategy")
    count = min(maximum, len(candidates))
    indexes = [index * (len(candidates) - 1) // max(count - 1, 1) for index in range(count)]
    body = "".join(source_text)
    probes: list[str] = []
    checked: dict[str, int] = {}
    for index in indexes:
        while index < len(candidates):
            candidate = candidates[index]
            if candidate not in checked:
                checked[candidate] = body.count(candidate)
            if checked[candidate] == 1 and candidate not in probes:
                probes.append(candidate)
                break
            index += 1
    if not probes:
        raise ValueError("No substring-unique source probes; choose another strategy")
    return probes, {"spine_documents": documents, "source_paragraphs": len(paragraphs), "paragraph_candidates": len(candidates), "substring_unique_probes": len(probes)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-tree", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source_tree = args.source_tree.resolve()
    sys.meta_path.insert(0, _SourceTreeFinder(source_tree))
    args.output.mkdir(parents=True, exist_ok=False)
    probes, source_info = source_probes(args.input)
    results = []
    for lane, action in (("book", _new_book), ("epub-md", _old_md), ("epub-html-md", _html_pipeline)):
        started = time.perf_counter()
        record = {"lane": lane}
        try:
            paths, _ = action(args.input, args.output / lane)
            text = _normalize("\n".join(BeautifulSoup(markdown.markdown(path.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"]), "html.parser").get_text() for path in paths))
            observations = [{"source_sha256": hashlib.sha256(probe.encode()).hexdigest(), "characters": len(probe), "occurrences": text.count(probe), "position": text.find(probe)} for probe in probes]
            present = [item["position"] for item in observations if item["occurrences"] == 1]
            record.update({"execution": "completed", "probes": observations, "exactly_once": sum(item["occurrences"] == 1 for item in observations), "probe_order_preserved": present == sorted(present)})
        except Exception as error:
            record.update({"execution": "error", "error_type": type(error).__name__, "error": str(error)})
        record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        results.append(record)
        print(f"{lane}: {record['execution']}, exactly-once probes={record.get('exactly_once', 'unknown')}/{len(probes)}", flush=True)
    report = {"source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(), "source_info": source_info, "probe_count": len(probes), "loaded_converter_modules": _module_origins(source_tree), "results": results, "limitations": ["Paragraph probes are sampled; this does not prove whole-book completeness", "Images, tables, links and layout require separate inspection", "Probe/source normalizations are whitespace and Unicode NFKC only"]}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
