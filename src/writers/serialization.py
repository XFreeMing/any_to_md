"""The only canonical DOM to Markdown serializer."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter


class _MarkdownSerializer(MarkdownConverter):
    def convert_figure(self, el, text, parent_tags):
        return '\n\n'+str(el)+'\n\n'

    def convert_figcaption(self, el, text, parent_tags):
        return '\n\n'+str(el)+'\n\n'

    def convert_video(self, el, text, parent_tags):
        return '\n\n'+str(el)+'\n\n'

    def convert_audio(self, el, text, parent_tags):
        return '\n\n'+str(el)+'\n\n'

    def convert_svg(self, el, text, parent_tags):
        return "\n\n" + str(el) + "\n\n"

    def convert_a(self, el, text, parent_tags):
        if el.get("id") and not el.get("href"):
            return f'\n\n<a id="{el["id"]}"></a>\n\n' + text
        converted = super().convert_a(el, text, parent_tags)
        return (f'<a id="{el["id"]}"></a>' if el.get('id') else '') + converted

    def convert_ol(self, el, text, parent_tags):
        if el.find(['ol', 'ul']) or el.find(id=True) or el.get('id') or el.get('start') or el.get('type') or el.find('li', value=True):
            return '\n\n'+str(el)+'\n\n'
        return super().convert_ol(el, text, parent_tags)

    def convert_ul(self, el, text, parent_tags):
        if el.find(['ol', 'ul']) or el.find(id=True) or el.get('id') or el.get('data-docx-list-instance'):
            return '\n\n'+str(el)+'\n\n'
        return super().convert_ul(el, text, parent_tags)

    def convert_dl(self, el, text, parent_tags):
        return '\n\n'+str(el)+'\n\n'

    def convert_table(self, el, text, parent_tags):
        complex_cells = any(int(cell.get("rowspan", 1)) > 1 or int(cell.get("colspan", 1)) > 1 for cell in el.find_all(["td", "th"]))
        rows = [row for row in el.find_all('tr') if row.find_parent('table') is el]
        header = rows[0].find_all(['th', 'td'], recursive=False) if rows else []
        simple_header = bool(header) and all(cell.name == 'th' for cell in header) and not any(row.find('th') for row in rows[1:])
        if complex_cells or not simple_header or el.find(["img", "a", "table"]):
            return "\n\n" + str(el) + "\n\n"
        return super().convert_table(el, text, parent_tags)

    def convert_img(self, el, text, parent_tags):
        if el.get('data-source-location') or el.get("srcset"):
            # Inline HTML keeps occurrence provenance and does not split a list
            # item into a new block when an image follows its text.
            return str(el)
        # Markdownify otherwise replaces images in headings/table cells with alt text.
        return super().convert_img(el, text, parent_tags - {"_inline"})

    def convert_pre(self, el, text, parent_tags):
        for line_break in el.find_all('br'):
            line_break.replace_with('\n')
        code = el.get_text().rstrip("\n")
        longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
        fence = "`" * max(3, longest + 1)
        child = el.find("code")
        language = ""
        if child:
            language = next((value[9:] for value in child.get("class", []) if value.startswith("language-")), "")
        return f"\n\n{fence}{language}\n{code}\n{fence}\n\n"


def to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in list(soup.find_all(id=True)):
        if tag.name in {"a", "table", "svg", "ol", "ul", "dl"} or tag.find_parent(["table", "svg", "ol", "ul", "dl"]):
            continue
        anchor = soup.new_tag("a", id=tag["id"])
        tag.insert_before(anchor)
        del tag["id"]
    return _MarkdownSerializer(heading_style="ATX", bullets="-").convert(str(soup)).strip()
