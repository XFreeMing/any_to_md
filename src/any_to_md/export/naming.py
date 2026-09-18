"""Cross-platform chapter file naming."""

from __future__ import annotations

import re
import unicodedata

_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{value}" for value in range(1, 10)),
    *(f"LPT{value}" for value in range(1, 10)),
}


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    encoded = encoded[:maximum_bytes]
    while encoded:
        try:
            return encoded.decode("utf-8").rstrip("-_. ")
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return ""


def title_slug(title: str) -> str:
    value = unicodedata.normalize("NFC", title)
    value = re.sub(r"[<>:\"/\\|?*#%\x00-\x1f\x7f]", "-", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value).strip(" .-_")
    value = _truncate_utf8(value, 96) or "未命名章节"
    if value.upper() in _RESERVED:
        value = f"_{value}"
    return value


def ordinal_name(ordinal_path: tuple[int, ...], width: int = 3) -> str:
    return "-".join(f"{value:0{width}d}" for value in ordinal_path)


def unique_short_ids(node_ids: list[str], minimum: int = 8) -> dict[str, str]:
    compact = {node_id: node_id.replace("-", "") for node_id in node_ids}
    result: dict[str, str] = {}
    for node_id, value in compact.items():
        length = minimum
        while any(
            other_id != node_id and other_value[:length] == value[:length]
            for other_id, other_value in compact.items()
        ):
            length += 1
        result[node_id] = value[:length]
    return result


def chapter_file_name(
    *,
    ordinal_path: tuple[int, ...],
    title: str,
    short_id: str,
    is_index: bool,
) -> str:
    ordinal = ordinal_name(ordinal_path)
    if is_index:
        ordinal = f"{ordinal}-000"
    suffix = "_index" if is_index else ""
    filename = f"{ordinal}_{title_slug(title)}--{short_id}{suffix}.md"
    return _truncate_utf8(filename, 180)
