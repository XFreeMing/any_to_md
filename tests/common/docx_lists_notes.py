"""Independently authored OOXML and reference tables for UC-06-S4."""

from zipfile import ZipFile

from tests.common.docx_fixtures import paragraph, W
from tests.common.fixtures import _archive, _docx, _image
from tests.common.inspection import image_identity
from tests.common.reuse_support import save, digest


FORMATS = [('decimal', None), ('decimalZero', None), ('lowerLetter', 'a'),
           ('upperLetter', 'A'), ('lowerRoman', 'i'), ('upperRoman', 'I'), ('bullet', None)]
KINDS = ('footnote', 'endnote', 'comment')


def samples(root):
    root.mkdir(parents=True)
    seed = root/'seed.docx'
    _docx(seed, _image('red'))
    with ZipFile(seed) as archive:
        base = {name: archive.read(name) for name in archive.namelist()}
    xml = base['word/document.xml'].decode()
    opening = xml.split('<w:body>')[0]
    drawing = '<w:drawing>'+xml.split('<w:drawing>')[1].split('</w:drawing>')[0]+'</w:drawing>'
    image = '<w:r>'+drawing+'</w:r>'
    def numbered(text, instance, level=0):
        return f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{instance}"/></w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
    def level(index, kind, start=1):
        return f'<w:lvl w:ilvl="{index}"><w:start w:val="{start}"/><w:numFmt w:val="{kind}"/><w:lvlText w:val="'+('•' if kind=='bullet' else f'%{index+1}.')+'"/></w:lvl>'
    def number(instance, abstract, start=None):
        return f'<w:num w:numId="{instance}"><w:abstractNumId w:val="{abstract}"/>'+('' if start is None else f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="{start}"/></w:lvlOverride>')+'</w:num>'
    def write(name, body, numbering='', extras=None):
        members = dict(base)
        members['word/document.xml'] = opening+'<w:body>'+body+'<w:sectPr/></w:body></w:document>'
        members['word/styles.xml'] = f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>'
        members['word/numbering.xml'] = f'<w:numbering xmlns:w="{W}">'+numbering+'</w:numbering>'
        relations = members['word/_rels/document.xml.rels'].decode()
        types = members['[Content_Types].xml'].decode()
        for part in ('styles', 'numbering', *(kind+'s' for kind in KINDS if extras)):
            relations = relations.replace('</Relationships>', f'<Relationship Id="{part}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{part}" Target="{part}.xml"/></Relationships>')
            types = types.replace('</Types>', f'<Override PartName="/word/{part}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{part}+xml"/></Types>')
        members['word/_rels/document.xml.rels'] = relations
        members['[Content_Types].xml'] = types
        if extras:
            members.update(extras)
        target = root/(name+'.docx')
        _archive(target, members)
        return target
    references = {}
    body, definitions, items = paragraph('Numbering', 'Heading1'), '', []
    for index, (kind, html_type) in enumerate(FORMATS, 1):
        definitions += f'<w:abstractNum w:abstractNumId="{index}">'+level(0, kind, 3)+'</w:abstractNum>'
        for instance, start, count in [(index*10, 3, 2), (index*10+1, 3, 1), (index*10+2, 7, 2), (index*10+3, 1, 1)]:
            definitions += number(instance, index, start)
            for offset in range(count):
                text = f'{kind} instance {instance} item {offset+1}'
                body += numbered(text, instance)
                items.append(dict(text=text, kind=kind, html_type=html_type, value=start+offset,
                                  level=0, instance=str(instance), parent=None, owner='Numbering',
                                  display=display(kind,start+offset)))
    write('numbering', body, definitions)
    references['numbering'] = dict(items=items, notes=[], images=[], absent=[])
    rows = [('Outer first',0,2,None), ('Bullet first',1,None,'Outer first'),
            ('Letter first',2,4,'Bullet first'), ('Letter second',2,5,'Bullet first'),
            ('Bullet second',1,None,'Outer first'), ('Letter restarted',2,4,'Bullet second'),
            ('Outer second',0,3,None), ('Bullet restarted',1,None,'Outer second'),
            ('Outer third',0,4,None)]
    definitions = '<w:abstractNum w:abstractNumId="1">'+level(0,'decimal',2)+level(1,'bullet')+level(2,'lowerLetter',4)+'</w:abstractNum>'+number(1,1)
    body = paragraph('Nested', 'Heading1')+''.join(numbered(text,1,depth) for text,depth,_,_ in rows)
    write('nested',body,definitions)
    references['nested'] = dict(items=[dict(text=text,level=depth,value=value,parent=parent,
        kind=['decimal','bullet','lowerLetter'][depth],html_type='a' if depth==2 else None,
        instance='1',owner='Nested',display=display(['decimal','bullet','lowerLetter'][depth],value)) for text,depth,value,parent in rows],notes=[],images=[],absent=[])
    # References are deliberately interleaved, repeated across two chapter files.
    refs = [(7,'first'),(7,'again'),(12,'first'),(9,'single'),(7,'return'),(12,'return'),(12,'third')]
    for variant in ('notes', 'inactive-only', 'lost-image'):
        body = paragraph('References A','Heading1')
        expected_notes, expected_images, extras, absent = [], [], {}, []
        reference_rows = []
        for occurrence,(identifier,label) in enumerate(refs):
            if occurrence==3:
                body += paragraph('References B','Heading1')
            for kind in KINDS:
                text = f'{kind} {identifier} {label} reference'
                reference_rows.append(dict(kind=kind,id=str(identifier),text=text,
                    owner='References A' if occurrence<3 else 'References B',
                    source=f'word/document.xml#body/p[{2+occurrence*3+(1 if occurrence>=3 else 0)+KINDS.index(kind)}]/r[1]/{kind}Reference[1]'))
                body += '<w:p><w:r><w:t>'+text+f'</w:t><w:{kind}Reference w:id="{identifier}"/></w:r></w:p>'
        for kind in KINDS:
            part = f'word/{kind}s.xml'
            notes = ''
            for ordinal, identifier in enumerate((7,9,12,99),1):
                text = f'{kind} {identifier} body'
                content = paragraph(text)
                if identifier==99:
                    absent.append(text)
                else:
                    expected_notes.append(dict(kind=kind,id=str(identifier),text=text,
                        label=({'7':'[Q1]','12':'[Q2]','9':'[Q3]'} if kind=='comment' else
                               {'7':'[1]','12':'[3]','9':'[5]'} if kind=='footnote' else
                               {'7':'[2]','12':'[4]','9':'[6]'})[str(identifier)],
                        references=[row for row in reference_rows if row['kind']==kind and row['id']==str(identifier)],
                        owner='References B'))
                image_count = 2 if identifier==7 else 1
                if variant=='inactive-only' and identifier!=99:
                    image_count=0
                if image_count:
                    # Same run/drawing shape and same bytes across note identities;
                    # two drawings in one run also exercise internal occurrence.
                    drawings = drawing*image_count
                    if variant=='lost-image' and identifier==7:
                        drawings = drawing.replace('r:embed="img1"','r:link="externalImg"')+drawing
                    if identifier in (12,99):
                        drawings = drawings.replace('img1','img2' if identifier==12 else 'img3')
                    content += '<w:p><w:r>'+drawings+'</w:r></w:p>'
                    if identifier!=99:
                        for internal in range(1,image_count+1):
                            location = f'{part}#{kind}[{ordinal}][id={identifier}]/p[2]/r[1]/drawing[{internal}]'
                            lost = variant=='lost-image' and identifier==7 and internal==1
                            expected_images.append(dict(kind=kind,id=str(identifier),location=location,
                                color='blue' if identifier==12 else 'red',lost=lost,owner='References B'))
                author = ' w:author="Reader" w:initials="Q"' if kind=='comment' else ''
                notes += f'<w:{kind} w:id="{identifier}"{author}>'+content+f'</w:{kind}>'
            extras[part] = f'<w:{kind}s '+opening.split('<w:document ',1)[1].split('>',1)[0]+'>'+notes+f'</w:{kind}s>'
            extras[f'word/_rels/{kind}s.xml.rels'] = base['word/_rels/document.xml.rels'].decode().replace('</Relationships>',
                '<Relationship Id="img2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/blue.png"/><Relationship Id="img3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/green.png#unused"/><Relationship Id="externalImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://example.invalid/lost.png" TargetMode="External"/></Relationships>')
        extras['word/media/blue.png'] = _image('blue')
        extras['word/media/green.png'] = _image('green')
        write(variant,body,extras=extras)
        references[variant] = dict(items=[],notes=expected_notes,images=expected_images,absent=absent,
                                  references=reference_rows)
    for name,reference in references.items():
        reference['source_sha256'] = digest(root/(name+'.docx'))
        reference['pixels'] = {color:image_identity(_image(color)) for color in ('red','blue','green')}
    save(root/'independent-reference.json', references)
    return references


def display(kind, value):
    """Tiny independent label oracle for the deliberately bounded values."""
    if kind=='bullet':
        return None
    if kind=='decimal':
        label=str(value)
    elif kind=='decimalZero':
        label=f'{value:02d}'
    elif kind in ('lowerLetter','upperLetter'):
        label=chr(ord('a')+value-1)
        if kind=='upperLetter':
            label=label.upper()
    else:
        label={1:'i',3:'iii',4:'iv',7:'vii',8:'viii'}[value]
        if kind=='upperRoman':
            label=label.upper()
    return label+'.'
