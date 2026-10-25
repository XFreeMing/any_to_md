"""Build small, self-authored source documents; never copy user books.

The expectations live in cases.json and are fixed independently of conversion
output. Generated archives have stable member metadata for reproducible hashes.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from PIL import Image, ImageDraw

MANIFEST = Path(__file__).with_name("cases.json")

STYLE = ".tint { color: #154c79; } .listing { font-family: monospace; }"
EXTERNAL_STYLE = ".external-only { border-left: 7px solid #c12345; padding-left: 11px; }"
RICH_BODY = '''<section id="first"><h1>第一章：图表与代码</h1>
<p class="tint external-only">BODY_FIRST_101 中文正文。</p>
<p><a href="#second">下一章</a> <a href="#note">脚注一</a></p>
<img src="assets/red.png" srcset="assets/red.png 1x, assets/blue.png 2x" alt="红蓝候选图"/>
<table><tr><th colspan="2">TABLE_HEADER_102</th></tr>
<tr><td rowspan="2">TABLE_SPAN_103</td><td><a href="#second">TABLE_LINK_104</a></td></tr>
<tr><td>TABLE_LAST_105 <img src="assets/blue.png" alt="表格图片"/></td></tr></table>
<pre><code># CODE_HASH_106
  value = "a_b * c"
  print(value)
</code></pre>
<h2 id="sub">第一节：同文件小节</h2><p>BODY_SUB_107</p>
</section><section id="second"><h1>第二章：同名锚点</h1>
<p>BODY_SECOND_108</p><p id="note">FOOTNOTE_109 <a href="#first">返回</a></p>
</section>'''


def load_cases() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]


def _archive(path: Path, members: dict[str, str | bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        for name, content in members.items():
            member = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            member.compress_type = ZIP_STORED if name == "mimetype" else ZIP_DEFLATED
            archive.writestr(member, content.encode() if isinstance(content, str) else content)


def _image(color: str) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (64, 32), color).save(stream, format="PNG")
    return stream.getvalue()


def _html(body: str, title: str = "质量样本") -> str:
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f'<meta charset="utf-8"/><title>{title}</title>'
        f'<style>{STYLE}</style></head><body>{body}</body></html>'
    )


def _epub(path: Path, kind: str, red: bytes, blue: bytes) -> None:
    if kind == "plain":
        documents = {"plain.xhtml": '<h1 id="start">普通章节</h1><p>PLAIN_BODY_001</p>'}
        navigation = '<li><a href="plain.xhtml#start">普通章节</a></li>'
    elif kind == "no_toc":
        documents = {
            "one.xhtml": '<h1>第一章</h1><p>NO_TOC_FIRST_201</p>',
            "two.xhtml": '<h1>第二章</h1><p>NO_TOC_SECOND_202</p>',
        }
        navigation = ""
    elif kind == "anchors":
        documents = {
            "one.xhtml": '<div><p>PREFIX_301</p><h1 id="start">第一章</h1>'
            '<p>ANCHOR_FIRST_302</p><h2 id="sub">子节</h2><p>ANCHOR_SUB_303</p></div>',
            "two.xhtml": '<h1 id="start">第二章</h1><p>ANCHOR_SECOND_304</p>'
            '<a href="one.xhtml#sub">返回子节</a>',
        }
        navigation = (
            '<li><a href="one.xhtml#start">第一章</a><ol>'
            '<li><a href="one.xhtml#sub">子节</a></li>'
            '<li><a href="one.xhtml#missing">缺失锚点</a></li></ol></li>'
            '<li><a href="two.xhtml#start">第二章</a></li>'
            '<li><a href="two.xhtml#start">第二章重复目录项</a></li>'
        )
    elif kind == "titles":
        long_title = "中文长标题" * 45
        documents = {
            "one.xhtml": '<h1 id="start">同名章节</h1><p>TITLE_FIRST_701</p>',
            "two.xhtml": '<h1 id="start">同名章节</h1><p>TITLE_SECOND_702</p>',
            "three.xhtml": f'<h1 id="start">{long_title}</h1><p>TITLE_LONG_703</p>',
        }
        navigation = (
            '<li><a href="one.xhtml#start">同名章节</a></li>'
            '<li><a href="two.xhtml#start">同名章节</a></li>'
            f'<li><a href="three.xhtml#start">{long_title}</a></li>'
        )
    else:
        first, second = RICH_BODY.split('<section id="second">', 1)
        documents = {
            "chapter_split_000.xhtml": first,
            "chapter_split_001.xhtml": '<p>BODY_CONTINUATION_110</p>',
            "second.xhtml": '<section id="second">' + second.replace('href="#first"', 'href="chapter_split_000.xhtml#first"'),
        }
        documents["chapter_split_000.xhtml"] = documents["chapter_split_000.xhtml"].replace(
            'href="#second"', 'href="second.xhtml#second"'
        )
        # The footnote belongs to the following document, too.
        documents["chapter_split_000.xhtml"] = documents["chapter_split_000.xhtml"].replace(
            'href="#note"', 'href="second.xhtml#note"'
        )
        navigation = (
            '<li><a href="chapter_split_000.xhtml#first">第一章：图表与代码</a><ol>'
            '<li><a href="chapter_split_000.xhtml#sub">第一节：同文件小节</a></li></ol></li>'
            '<li><a href="second.xhtml#second">第二章：同名锚点</a></li>'
        )
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    spine = []
    members: dict[str, str | bytes] = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "OEBPS/assets/red.png": red,
        "OEBPS/assets/blue.png": blue,
        "OEBPS/style.css": EXTERNAL_STYLE,
        "OEBPS/nav.xhtml": _html('<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc"><ol>' + navigation + '</ol></nav>'),
    }
    for index, (name, body) in enumerate(documents.items()):
        manifest.append(f'<item id="doc{index}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="doc{index}"/>')
        members[f"OEBPS/{name}"] = _html(body).replace('</head>', '<link rel="stylesheet" href="style.css"/></head>')
    manifest.extend([
        '<item id="red" href="assets/red.png" media-type="image/png"/>',
        '<item id="blue" href="assets/blue.png" media-type="image/png"/>',
        '<item id="style" href="style.css" media-type="text/css"/>',
    ])
    members["OEBPS/package.opf"] = (
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book">quality-' + kind + '</dc:identifier>'
        '<dc:title>质量样本</dc:title><dc:language>zh</dc:language>'
        '<meta property="dcterms:modified">2020-01-01T00:00:00Z</meta></metadata><manifest>'
        + ''.join(manifest) + '</manifest><spine>' + ''.join(spine) + '</spine></package>'
    )
    _archive(path, members)


def _docx(path: Path, image: bytes) -> None:
    paragraphs = ''.join(
        '<w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>'
        for text in ["DOCX_FIRST_401 中文段落", "DOCX_SECOND_402"]
    )
    drawing = '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="609600" cy="304800"/><wp:docPr id="1" name="quality-image"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="red.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="img1"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="609600" cy="304800"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    table = '<w:tbl><w:tblGrid><w:gridCol w="2000"/><w:gridCol w="2000"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>DOCX_TABLE_403</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    _archive(path, {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="doc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/_rels/document.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="img1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/red.png"/></Relationships>',
        "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>' + paragraphs + drawing + table + '<w:sectPr/></w:body></w:document>',
        "word/media/red.png": image,
    })


def _pdf(path: Path, mode: str) -> None:
    import pymupdf

    count = 5 if mode == "mixed" else 2
    with pymupdf.open() as document:
        for index in range(count):
            text = f"PDF_{mode.upper()}_PAGE_{index + 1} " + 'quality source sentence ' * 12
            with pymupdf.open() as template:
                page = template.new_page(width=420, height=420)
                page.insert_textbox(pymupdf.Rect(32, 32, 388, 350), text, fontsize=12)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                output = document.new_page(width=420, height=420)
                if mode == "scanned" or (mode == "mixed" and index < 3):
                    output.insert_image(output.rect, stream=pixmap.tobytes("png"))
                else:
                    output.insert_textbox(pymupdf.Rect(32, 32, 388, 350), text, fontsize=12)
        document.save(path, no_new_id=True, garbage=4, deflate=True)


def build_fixtures(root: Path) -> dict[str, Path]:
    """Return case IDs mapped to generated input paths."""
    root.mkdir(parents=True, exist_ok=True)
    red, blue = _image("red"), _image("blue")
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "red.png").write_bytes(red)
    (assets / "blue.png").write_bytes(blue)
    result: dict[str, Path] = {}
    for case in load_cases():
        path = root / case["filename"]
        kind = case["kind"]
        if kind == "epub":
            _epub(path, case["variant"], red, blue)
        elif kind in {"html", "url"}:
            body = RICH_BODY if kind == "html" else (
                '<article><h1>网页标题</h1>'
                + ''.join(f'<p>URL_PARAGRAPH_{index}_501 这是用于正文提取的独立中文段落，说明转换内容与资源必须保留。' + 'Self authored article content. ' * 8 + '</p>' for index in range(1, 5))
                + '<img src="assets/red.png" alt="正文配图"/></article>'
            )
            path.write_text(_html(body), encoding="utf-8")
        elif kind == "docx":
            _docx(path, red)
        elif kind == "pdf":
            _pdf(path, case["variant"])
        elif kind == "image":
            image = Image.new("RGB", (900, 180), "white")
            ImageDraw.Draw(image).text((30, 40), "IMAGE_OCR_601 source text", fill="black", font_size=32)
            image.save(path)
        else:
            raise ValueError(f"Unknown fixture kind: {kind}")
        result[case["id"]] = path
    return result
