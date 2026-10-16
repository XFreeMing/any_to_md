"""Independent OOXML sources and frozen DOCX structure expectations."""

from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

from .fixtures import _archive, _docx, _image

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def paragraph(text: str, style: str | None = None) -> str:
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
    return f'<w:p>{properties}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def structured_docx(path: Path) -> dict:
    _docx(path, _image('red'))
    with ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    original = members['word/document.xml'].decode()
    drawing = '<w:p><w:r><w:drawing>'+original.split('<w:drawing>', 1)[1].split('</w:drawing>', 1)[0]+'</w:drawing></w:r></w:p>'
    opening = original.split('<w:body>', 1)[0]
    def numbered(text, level):
        return f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
    head = '<w:tc><w:tcPr><w:gridSpan w:val="3"/></w:tcPr>'+paragraph('DOCX_TABLE_HEAD_506')+'</w:tc>'
    span = '<w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>'+paragraph('DOCX_TABLE_SPAN_507')+'</w:tc>'
    continuation = '<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>'
    image_cell = '<w:tc>'+drawing.replace('img1', 'img2')+paragraph('DOCX_TABLE_IMAGE_508')+'</w:tc>'
    last_cell = '<w:tc>'+paragraph('DOCX_TABLE_LAST_510')+'</w:tc>'
    linked_cell = '<w:tc><w:p><w:hyperlink w:anchor="SecondSection"><w:r><w:t>DOCX_TABLE_LINK_509</w:t></w:r></w:hyperlink></w:p></w:tc>'
    table = '<w:tbl><w:tblGrid>'+('<w:gridCol w:w="2000"/>'*3)+'</w:tblGrid><w:tr><w:trPr><w:tblHeader/></w:trPr>'+head+'</w:tr><w:tr>'+span+image_cell+'<w:tc>'+paragraph('DOCX_TABLE_SIDE_523')+'</w:tc></w:tr><w:tr>'+continuation+linked_cell+last_cell+'</w:tr></w:tbl>'
    bookmark = '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:bookmarkStart w:id="0" w:name="SecondSection"/><w:r><w:t>第二章：结构</w:t></w:r><w:bookmarkEnd w:id="0"/></w:p>'
    notes = '<w:p><w:r><w:t>DOCX_NOTE_REF_514</w:t><w:footnoteReference w:id="1"/><w:endnoteReference w:id="2"/></w:r></w:p>'
    comment = '<w:p><w:r><w:t>DOCX_COMMENT_REF_515</w:t><w:commentReference w:id="3"/></w:r></w:p>'
    body = ''.join([
        paragraph('DOCX_INTRO_501 中文引言'), paragraph('第一章：内容', 'Heading1'), paragraph('DOCX_FIRST_502'),
        numbered('DOCX_LIST_TOP_503', 0), numbered('DOCX_LIST_NESTED_504', 1), numbered('DOCX_LIST_RETURN_505', 0),
        drawing, table, paragraph('# DOCX_CODE_511', 'CodeBlock'), paragraph('  value = "a_b * c"', 'CodeBlock'),
        paragraph('  print(value)', 'CodeBlock'), paragraph('第一节：细节', 'Heading2'), paragraph('DOCX_SUB_512'),
        bookmark, paragraph('DOCX_SECOND_513'), notes, comment,
        '<w:p><w:hyperlink r:id="link1"><w:r><w:t>DOCX_EXTERNAL_LINK_516</w:t></w:r></w:hyperlink></w:p>',
        paragraph('DOCX_TAIL_517'),
    ])
    members['word/document.xml'] = opening+'<w:body>'+body+'<w:sectPr/></w:body></w:document>'
    members['word/media/blue.png'] = _image('blue')
    types = members['[Content_Types].xml'].decode()
    relations = members['word/_rels/document.xml.rels'].decode()
    for name, kind in [('styles', 'styles'), ('numbering', 'numbering'), ('footnotes', 'footnotes'), ('endnotes', 'endnotes'), ('comments', 'comments')]:
        types = types.replace('</Types>', f'<Override PartName="/word/{name}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/></Types>')
        relations = relations.replace('</Relationships>', f'<Relationship Id="{name}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{name}.xml"/></Relationships>')
    relations = relations.replace('</Relationships>', '<Relationship Id="img2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/blue.png"/><Relationship Id="link1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/document" TargetMode="External"/></Relationships>')
    members['[Content_Types].xml'] = types
    members['word/_rels/document.xml.rels'] = relations
    members['word/styles.xml'] = f'<w:styles xmlns:w="{W}">'+''.join(f'<w:style w:type="paragraph" w:styleId="{identifier}"><w:name w:val="{name}"/></w:style>' for identifier, name in [('Heading1', 'heading 1'), ('Heading2', 'heading 2'), ('CodeBlock', 'Code Block')])+'</w:styles>'
    members['word/numbering.xml'] = f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl><w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    members['word/footnotes.xml'] = f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="1">'+paragraph('DOCX_FOOTNOTE_518 中文脚注')+'</w:footnote></w:footnotes>'
    members['word/endnotes.xml'] = f'<w:endnotes xmlns:w="{W}"><w:endnote w:id="2">'+paragraph('DOCX_ENDNOTE_519')+'</w:endnote></w:endnotes>'
    members['word/comments.xml'] = f'<w:comments xmlns:w="{W}"><w:comment w:id="3" w:author="审阅者" w:initials="Q">'+paragraph('DOCX_COMMENT_520')+'</w:comment></w:comments>'
    members['docProps/core.xml'] = '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>结构与资源质量样本</dc:title><dc:creator>质量作者</dc:creator><dc:language>zh-CN</dc:language></cp:coreProperties>'
    _archive(path, members)
    return {
        'markers': ['DOCX_INTRO_501', 'DOCX_FIRST_502', 'DOCX_LIST_TOP_503', 'DOCX_LIST_NESTED_504', 'DOCX_LIST_RETURN_505', 'DOCX_TABLE_HEAD_506', 'DOCX_TABLE_SPAN_507', 'DOCX_TABLE_IMAGE_508', 'DOCX_TABLE_SIDE_523', 'DOCX_TABLE_LINK_509', 'DOCX_TABLE_LAST_510', 'DOCX_CODE_511', 'DOCX_SUB_512', 'DOCX_SECOND_513', 'DOCX_NOTE_REF_514', 'DOCX_COMMENT_REF_515', 'DOCX_EXTERNAL_LINK_516', 'DOCX_TAIL_517', 'DOCX_FOOTNOTE_518', 'DOCX_ENDNOTE_519', 'DOCX_COMMENT_520'],
        'minimum_images': 2,
        'image_pixels': ['c24b766a4b9160e0c2b9de46b17ae5926100702b233052bfb684cabd7d01390d', '2dcdc8df5bacc622ac25611f014794e578de3174c3eca9399247d5c3fe290811'],
        'code_contains': '  value = "a_b * c"',
        'required_cells': [{'marker': 'DOCX_TABLE_HEAD_506', 'row': 0, 'column': 0, 'rowspan': 1, 'colspan': 3}, {'marker': 'DOCX_TABLE_SPAN_507', 'row': 1, 'column': 0, 'rowspan': 2, 'colspan': 1}, {'marker': 'DOCX_TABLE_LINK_509', 'row': 2, 'column': 1, 'rowspan': 1, 'colspan': 1}],
        'required_links': [{'label': 'DOCX_TABLE_LINK_509', 'target_marker': 'DOCX_SECOND_513'}, {'label': '[1]', 'target_marker': 'DOCX_FOOTNOTE_518'}, {'label': '[2]', 'target_marker': 'DOCX_ENDNOTE_519'}, {'label': '[Q1]', 'target_marker': 'DOCX_COMMENT_520'}],
    }
