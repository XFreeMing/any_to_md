"""Resolve one current Markdown corpus per book from the catalog and export folders.

The pointer lives in catalog.sqlite. Historical export directories stay where they are.
library/ at the repository root only stores links to that current directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..adapters.persistence.sqlite import SQLiteCatalog
from ..adapters.storage import LocalStorage
from ..export.naming import title_slug

_READY = {"completed", "completed_with_warnings"}
_MARKS = ("（", "(", "：", ":", "—", "–", "【", "[")


@dataclass(frozen=True)
class CurrentBook:
    book_id: str
    title: str
    authors: tuple[str, ...]
    filenames: tuple[str, ...]
    export_id: str
    directory: Path
    published_at: str
    library_name: str = ""


def library_dir_for(data_dir: Path, library_dir: Path | None = None) -> Path:
    """Links sit beside the data directory: `<repo>/library` when data is `<repo>/var`."""
    data = data_dir.expanduser().resolve()
    chosen = library_dir.expanduser().resolve() if library_dir is not None else data.parent / "library"
    if chosen == data or chosen.is_relative_to(data):
        raise ValueError("library 必须在数据目录之外，例如仓库根目录的 library/")
    return chosen


def short_title(title: str) -> str:
    value = unicodedata.normalize("NFC", title).strip()
    cuts = [value.find(mark) for mark in _MARKS]
    cuts = [index for index in cuts if index > 0]
    if cuts:
        value = value[: min(cuts)].strip()
    return value or title.strip() or "未命名"


def corpus_ready(directory: Path) -> bool:
    """book.md, TOC.md, chapters/ and manifest.json, with no symlink stand-ins."""
    if not directory.is_dir() or directory.is_symlink():
        return False
    required = ("book.md", "TOC.md", "manifest.json")
    if any((directory / name).is_symlink() or not (directory / name).is_file() for name in required):
        return False
    chapters = directory / "chapters"
    if chapters.is_symlink() or not chapters.is_dir():
        return False
    return any(path.is_file() and not path.is_symlink() for path in chapters.rglob("*.md"))


def corpus_fingerprint(directory: Path) -> str:
    """Identity of the readable Markdown. manifest.json embeds export_id, so it is not compared."""
    digest = hashlib.sha256()
    paths = [directory / "book.md", directory / "TOC.md"]
    paths.extend(sorted(path for path in (directory / "chapters").rglob("*") if path.is_file()))
    for path in paths:
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _open(data_dir: Path) -> tuple[SQLiteCatalog, LocalStorage]:
    storage = LocalStorage(data_dir)
    return SQLiteCatalog(storage.root / "catalog.sqlite"), storage


def _exported_directory(storage: LocalStorage, storage_key: str | None) -> Path | None:
    if not storage_key:
        return None
    try:
        path = storage.resolve(storage_key)
        path.relative_to(storage.exports_dir)
    except ValueError:
        return None
    return path


def _authors(raw: str) -> tuple[str, ...]:
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(loaded, list):
        return ()
    return tuple(author for author in loaded if isinstance(author, str))


def _eligible(storage: LocalStorage, row: dict) -> Path | None:
    if row.get("status") not in _READY or row.get("revision_status") != "ready":
        return None
    if not row.get("published_at") or not row.get("storage_key"):
        return None
    directory = _exported_directory(storage, row["storage_key"])
    if directory is None or not corpus_ready(directory):
        return None
    return directory


def _canonical_row(catalog: SQLiteCatalog, storage: LocalStorage, book_id: str) -> tuple[dict, Path] | None:
    snapshot = catalog.markdown_catalog()
    pointers = [row for row in snapshot["pointers"] if row["book_id"] == book_id]
    if pointers:
        directory = _eligible(storage, pointers[0])
        if directory is not None:
            return pointers[0], directory
    for row in snapshot["exports"]:
        if row["book_id"] != book_id:
            continue
        directory = _eligible(storage, row)
        if directory is not None:
            return row, directory
    return None


def choose_current_export(
    catalog: SQLiteCatalog,
    storage: LocalStorage,
    book_id: str,
    export_id: str,
    directory: Path,
    status: str,
) -> str | None:
    """Export id to store as the single current pointer, or None to leave the pointer untouched.

    Identical Markdown keeps the existing directory current. A different corpus moves the pointer.
    The new job may still publish its own historical directory.
    """
    if status not in _READY or not corpus_ready(directory):
        return None
    if _exported_directory(storage, directory.relative_to(storage.root).as_posix()) is None:
        return None
    current = _canonical_row(catalog, storage, book_id)
    if current is None:
        return export_id
    _row, current_dir = current
    if corpus_fingerprint(directory) == corpus_fingerprint(current_dir):
        return _row["export_id"]
    return export_id


def list_current(data_dir: Path) -> tuple[list[CurrentBook], list[dict]]:
    catalog, storage = _open(data_dir)
    snapshot = catalog.markdown_catalog()
    filenames: dict[str, list[str]] = {}
    for row in snapshot["filenames"]:
        filenames.setdefault(row["book_id"], [])
        if row["original_filename"] not in filenames[row["book_id"]]:
            filenames[row["book_id"]].append(row["original_filename"])
    resolved: list[CurrentBook] = []
    missing: list[dict] = []
    for book in snapshot["books"]:
        found = _canonical_row(catalog, storage, book["book_id"])
        if found is None:
            missing.append({"book_id": book["book_id"], "title": book["title"]})
            continue
        row, directory = found
        resolved.append(
            CurrentBook(
                book_id=book["book_id"],
                title=book["title"],
                authors=_authors(book["authors_json"]),
                filenames=tuple(filenames.get(book["book_id"], ())),
                export_id=row["export_id"],
                directory=directory,
                published_at=row["published_at"],
            )
        )
    return _named(resolved), missing


def _named(books: list[CurrentBook]) -> list[CurrentBook]:
    taken: set[str] = set()
    named: list[CurrentBook] = []
    for book in sorted(books, key=lambda item: item.book_id):
        base = title_slug(short_title(book.title))
        name = base
        if name in taken and book.authors:
            name = title_slug(f"{short_title(book.title)}-{book.authors[0]}")
        if name in taken:
            name = title_slug(f"{short_title(book.title)}-{book.book_id.split('-')[0]}")
        suffix = 2
        candidate = name
        while candidate in taken:
            candidate = title_slug(f"{name}-{suffix}")
            suffix += 1
        taken.add(candidate)
        named.append(
            CurrentBook(
                book.book_id,
                book.title,
                book.authors,
                book.filenames,
                book.export_id,
                book.directory,
                book.published_at,
                candidate,
            )
        )
    return named


def matches(book: CurrentBook, query: str) -> bool:
    needle = query.casefold().strip()
    if not needle:
        return False
    if book.book_id.casefold() == needle:
        return True
    title = book.title.casefold()
    short = short_title(book.title).casefold()
    slug = title_slug(short_title(book.title)).casefold()
    if needle in {title, short, slug} or (len(needle) >= 2 and needle in title):
        return True
    if any(needle == author.casefold() for author in book.authors):
        return True
    for filename in book.filenames:
        if needle in {filename.casefold(), Path(filename).stem.casefold()}:
            return True
    return False


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _remove_link(path: Path) -> None:
    if path.is_junction():
        os.rmdir(path)
        return
    path.unlink()


def _place_link(link: Path, target: Path) -> None:
    if _is_link(link):
        try:
            same = link.resolve() == target.resolve()
        except OSError:
            same = False
        if same:
            return
        _remove_link(link)
    elif link.exists():
        raise FileExistsError(f"图书馆路径已存在且不是链接：{link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # cmd parses one command string; quoting keeps spaces and non-ASCII names intact.
        command = f'mklink /J "{link}" "{target.resolve()}"'
        completed = subprocess.run(["cmd", "/c", command], capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise OSError(detail or "无法创建目录联接")
        return
    link.symlink_to(os.path.relpath(target.resolve(), link.parent.resolve()), target_is_directory=True)


def materialize(data_dir: Path, library_dir: Path | None = None) -> tuple[list[CurrentBook], list[dict]]:
    """Create or refresh `<repo>/library/<name>` links. Does not write a second catalog."""
    resolved, missing = list_current(data_dir)
    root = library_dir_for(data_dir, library_dir)
    storage = LocalStorage(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    wanted = {book.library_name: book.directory for book in resolved}
    for entry in root.iterdir():
        if entry.name in wanted or not _is_link(entry):
            continue
        try:
            entry.resolve().relative_to(storage.exports_dir)
        except (OSError, ValueError):
            continue
        _remove_link(entry)
    for name, target in wanted.items():
        _place_link(root / name, target)
    return resolved, missing
