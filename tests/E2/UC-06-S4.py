"""UC-06-S4 · E2 / US-09: Word 列表与注释正确关联。

Given 独立 OOXML 编号清单、嵌套顺序和三类注释的引用及逐次图片参考。
When 经完整转换生成 Markdown、HTML、PDF及适用分章，并经 API 重用资料。
Then 原三个 Examples 全部核对编号/层级/返回、单次和重复引用、图片归属、
逐次定位及真实损失诊断；未引用内容不交付，旧修订、原件和下载保持。
"""

if __name__ == '__main__' and not __package__:
    import runpy
    from pathlib import Path
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'common' / 'bootstrap.py'))

import json
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from src.application.convert import ConversionRequest, convert
from src.application.jobs import JobRuntime
from tests.common.conversion_support import api, submit, published, file_hashes
from tests.common.delivery_support import require_visual_evidence, visual_identity
from tests.common.docx_lists_notes import samples, KINDS
from tests.common.inspection import _soup, image_identity
from tests.common.reuse_support import save, digest, database


def own_text(element):
    clone = BeautifulSoup(str(element),'html.parser')
    for child in list(clone.find_all(['ol','ul'])):
        child.decompose()
    return clone.get_text(' ',strip=True)


def check_view(documents, reference, root, book=None):
    """Observe a complete single view or split collection against the source table."""
    failures, observation = [], {'lists':[], 'notes':[], 'images':[], 'links':[]}
    def expect(condition, detail):
        if not condition:
            failures.append(detail)
    text = '\n'.join(soup.get_text(' ',strip=True) for _,soup in documents)
    order=[r['text'] for r in reference['items']]
    if reference['notes']:
        order=[r['text'] for r in reference['references']]
        order += [f'{kind} {identifier} body' for identifier in (7,12,9) for kind in ('footnote','endnote')]
        order += [f'comment {identifier} body' for identifier in (7,12,9)]
    expect([text.find(value) for value in order]==sorted(text.find(value) for value in order) and all(text.count(value)==1 for value in order), {'body_order':order})
    for absent in reference['absent']:
        expect(absent not in text, {'unreferenced_body':absent})
    elements = [(path,element) for path,soup in documents for element in soup.find_all(True)]
    def owner(path,element):
        heading = element.find_previous(['h1','h2','h3'])
        return heading.get_text(' ',strip=True) if heading else None
    for row in reference['items']:
        matches = [(p,e) for p,e in elements if e.name=='li' and own_text(e)==row['text']]
        expect(len(matches)==1, {'list_occurrences':row['text'],'actual':len(matches)})
        if len(matches)!=1:
            continue
        path,item=matches[0]
        listing=item.find_parent(['ol','ul'])
        ancestors=item.find_parents(['ol','ul'])
        parent=item.find_parent('li')
        siblings=listing.find_all('li',recursive=False)
        value=int(listing.get('start','1'))
        for sibling in siblings:
            value=int(sibling.get('value',value))
            if sibling is item:
                break
            value+=1
        actual=dict(text=row['text'],level=len(ancestors)-1,parent=own_text(parent) if parent else None,
            kind=listing.name,html_type=listing.get('type'),value=value,owner=owner(path,item))
        observation['lists'].append(actual)
        expect(actual['level']==row['level'] and actual['parent']==row['parent'], {'nesting':actual,'expected':row})
        expect(listing.name==('ul' if row['kind']=='bullet' else 'ol'), {'list_kind':actual,'expected':row})
        expect(actual['html_type']==row['html_type'], {'numbering_type':actual,'expected':row})
        expect(actual['owner']==row['owner'], {'list_owner':actual,'expected':row})
        if row['kind']!='bullet':
            expect(value==row['value'], {'numbering_value':actual,'expected':row})
        if row['kind']=='decimalZero':
            expect('decimal-leading-zero' in listing.get('style',''), {'leading_zero':actual})
    observed_order=[own_text(e) for _,e in elements if e.name=='li' and own_text(e) in {r['text'] for r in reference['items']}]
    expect(observed_order==[r['text'] for r in reference['items']], {'list_order':observed_order})
    # Distinct source instances must not become a single continuous list.
    for previous,current in zip(reference['items'],reference['items'][1:]):
        if previous['instance']==current['instance']:
            continue
        def listing_for(row):
            return next((e.find_parent(['ol','ul']) for _,e in elements if e.name=='li' and own_text(e)==row['text']),None)
        expect(listing_for(previous) is not listing_for(current), {'merged_instances':[previous,current]})
    anchor_index={}
    for path,element in elements:
        if element.get('id'):
            anchor_index.setdefault((path,element['id']),[]).append(element)
    def target(path,href):
        parsed=urlsplit(href)
        destination=(path.parent/unquote(parsed.path)).resolve() if parsed.path else path
        matches=anchor_index.get((destination,unquote(parsed.fragment)),[])
        expect(len(matches)==1, {'link':href,'from':str(path),'target_count':len(matches)})
        return destination,matches[0] if len(matches)==1 else None
    for row in reference['notes']:
        bodies=[(p,e) for p,e in elements if e.name==('dt' if row['kind']=='comment' else 'li') and row['kind']+'-'+row['id'] in e.get('data-source-original-id','')]
        # Rendered IDs are namespaced but retain the source note suffix.
        if not bodies:
            bodies=[(p,e) for p,e in elements if e.name==('dd' if row['kind']=='comment' else 'li') and row['text'] in e.get_text(' ',strip=True)]
        expect(len(bodies)==1 and text.count(row['text'])==1, {'note_body_count':row['text'],'actual':len(bodies),'text_count':text.count(row['text'])})
        if len(bodies)!=1:
            continue
        path,note=bodies[0]
        if note.name=='dt':
            note=note.find_next_sibling('dd')
        expect(note is not None, {'missing_note_definition':row})
        if note is None:
            continue
        expect(owner(path,note)==row['owner'], {'note_owner':row['text'],'actual':owner(path,note)})
        resolved_refs=[]
        for ref in row['references']:
            matches=[(p,e) for p,e in elements if e.name=='p' and ref['text'] in e.get_text(' ',strip=True)]
            expect(len(matches)==1, {'reference_paragraph':ref,'actual':len(matches)})
            if len(matches)!=1:
                continue
            ref_path,paragraph=matches[0]
            links=paragraph.find_all('a',href=True)
            expect(len(links)==1, {'reference_link':ref,'actual':len(links)})
            if len(links)!=1:
                continue
            link=links[0]
            expect(owner(ref_path,paragraph)==ref['owner'], {'reference_owner':ref})
            destination,anchor=target(ref_path,link['href'])
            target_body=anchor.find_next_sibling('dd') if anchor is not None and anchor.name=='dt' else anchor
            expect(target_body is note, {'wrong_note_target':ref,'href':link['href']})
            ref_anchor=link if link.get('id') else link.find_previous_sibling('a')
            expect(ref_anchor is not None and ref_anchor.get('id'), {'missing_reference_anchor':ref})
            back=[]
            for back_link in note.find_all('a',href=True):
                bp,be=target(path,back_link['href'])
                if bp==ref_path and be is ref_anchor:
                    back.append(back_link)
            expect(len(back)==1, {'return_link_count':ref,'actual':len(back)})
            resolved_refs.append(dict(text=ref['text'],label=link.get_text(),href=link['href'],id=ref_anchor.get('id') if ref_anchor else None))
            expect(link.get_text()==row['label'], {'reference_label':ref,'actual':link.get_text(),'expected':row['label']})
        expect(len({ref['label'] for ref in resolved_refs})==1, {'repeated_labels':resolved_refs})
        expect(len({ref['id'] for ref in resolved_refs})==len(row['references']), {'reference_ids':resolved_refs})
        expect(len(note.find_all('a',href=True))==len(row['references']), {'extra_return_links':row['text']})
        observation['notes'].append(dict(text=row['text'],references=resolved_refs))
        note_images=note.find_all('img')
        expected=[r for r in reference['images'] if r['kind']==row['kind'] and r['id']==row['id'] and not r['lost']]
        expect(len(note_images)==len(expected), {'note_image_count':row['text'],'actual':len(note_images),'expected':len(expected)})
        for image,expected_image in zip(note_images,expected):
            location=image.get('data-source-location')
            expect(location==expected_image['location'], {'image_location':location,'expected':expected_image})
            if book is not None:
                asset=next((a for a in book['assets'] if image['src']=='asset://'+a['asset_id']),None)
                pixel=image_identity((root/'assets'/asset['file_name']).read_bytes()) if asset else None
                expect(asset is not None, {'missing_canonical_asset':image['src']})
                expect(pixel==reference['pixels'][expected_image['color']], {'canonical_image_pixels':pixel,'expected':expected_image})
            else:
                file=(path.parent/unquote(image['src'])).resolve()
                expect(file.is_relative_to(root.resolve()) and file.is_file(), {'image_resource':str(file)})
                pixel=image_identity(file.read_bytes()) if file.is_file() else None
                expect(pixel==reference['pixels'][expected_image['color']], {'image_pixels':pixel,'expected':expected_image})
            observation['images'].append(dict(note=row['text'],location=location,resource=image['src'],pixel=pixel))
    expect(len([e for _,e in elements if e.name=='img'])==sum(not r['lost'] for r in reference['images']), 'Unexpected or missing delivered image occurrences')
    return observation,failures


def check_export(export, reference):
    book=json.loads((export/'book.json').read_text(encoding='utf-8'))
    observations,failures={},[]
    canonical=BeautifulSoup(''.join('<h2>'+next(n['title_original'] for n in book['nodes'] if n['node_id']==c['node_id'])+'</h2>'+c['html'] for c in book['content']),'html.parser')
    # A canonical fragment does not rewrite cross-file links and has one combined document.
    obs,errors=check_view([(export/'canonical',canonical)],reference,export,book)
    observations['canonical']=obs
    failures.extend({'view':'canonical','detail':error} for error in errors)
    views={'markdown':[export/'book.md'],'html':[export/'book.html'],
           'chapters':sorted((export/'chapters').glob('*.md'))}
    for name,paths in views.items():
        if not paths or not all(path.is_file() for path in paths):
            continue
        obs,errors=check_view([(p.resolve(),_soup(p)) for p in paths],reference,export)
        observations[name]=obs
        failures.extend({'view':name,'detail':error} for error in errors)
    pdf=export/'book.pdf'
    if pdf.is_file():
        import pymupdf
        with pymupdf.open(pdf) as document:
            text='\n'.join(p.get_text() for p in document)
            # Numbered labels are visually reviewed and stored by page, rather than
            # inferring them from HTML. PDF destinations are checked below.
            for row in reference['items']+reference['notes']:
                assert text.count(row['text'])==1, (row['text'],text)
            assert all(value not in text for value in reference['absent'])
            links=[link for page in document for link in page.get_links()]
            expected_links=2*len(reference.get('references',[]))
            assert len([link for link in links if link.get('page',-1)>=0])>=expected_links,(len(links),expected_links)
            pdf_observation=check_pdf(document,reference,_soup(export/'book.html'))
            observations['pdf']={'pages':len(document),'text':text,'links':[{k:(list(v) if isinstance(v,(pymupdf.Rect,pymupdf.Point)) else v) for k,v in link.items()} for link in links],
                                 'images_by_page':[len(p.get_image_info()) for p in document], 'semantics':pdf_observation}
    losses=[r for r in reference['images'] if r['lost']]
    diagnostics=book['diagnostics']
    if losses:
        for row in losses:
            if not any(d['code']=='docx_image_location' and d['severity']=='error' and d['source_path']==row['location'] for d in diagnostics):
                failures.append({'missing_loss_diagnostic':row,'actual':diagnostics})
        if sum(d['code']=='docx_image_location' for d in diagnostics)!=len(losses):
            failures.append({'loss_diagnostic_count':diagnostics})
    else:
        if diagnostics:
            failures.append({'unexpected_diagnostics':diagnostics})
    return observations,failures


def check_pdf(document, reference, html):
    """Bind visible labels and PDF link rectangles to the actual text positions."""
    import pymupdf
    records=[]
    def find(text):
        matches=[(i,rect) for i,page in enumerate(document) for rect in page.search_for(text)]
        assert len(matches)==1,(text,matches)
        return matches[0]
    links=[(i,link) for i,page in enumerate(document) for link in page.get_links()]
    for row in reference['items']:
        page,rect=find(row['text'])
        words=document[page].get_text('words')
        labels=[w[4] for w in words if w[2]<=rect.x0+1 and abs((w[1]+w[3])/2-(rect.y0+rect.y1)/2)<5]
        assert row['display'] in labels if row['display'] else any(label in {'•','◦','▪'} for label in labels),(row,labels)
        records.append(dict(text=row['text'],page=page+1,rectangle=list(rect),labels=labels))
    names=document.resolve_names()
    def destination(identifier):
        target=names[identifier]
        page=target['page']
        assert 0<=page<len(document),(identifier,target)
        # Named PDF destinations use PDF coordinates (origin at bottom left).
        return page,document[page].rect.height-target['to'][1]
    note_starts=[]
    for row in reference['notes']:
        anchor=html.find(attrs={'data-source-original-id':row['kind']+'-'+row['id']})
        assert anchor is not None,row
        page,y=destination(anchor['id'])
        note_starts.append((page*10000+y,row,anchor))
    note_starts.sort(key=lambda item:item[0])
    for index,(start,row,note) in enumerate(note_starts):
        end=note_starts[index+1][0] if index+1<len(note_starts) else len(document)*10000
        body_page,body_rect=find(row['text'])
        target_page,target_y=destination(note['id'])
        assert body_page==target_page and -2<=body_rect.y0-target_y<65,(row,body_rect,target_y)
        for ref in row['references']:
            paragraph=next(p for p in html.find_all('p') if ref['text'] in p.get_text())
            link=paragraph.find('a',href=True)
            ref_page,ref_rect=find(ref['text'])
            outgoing=[(i,l) for i,l in links if i==ref_page and l.get('nameddest')==link['href'][1:] and
                      abs(l['from'].y0-ref_rect.y0)<8 and l['from'].x0>=ref_rect.x1-2]
            assert len(outgoing)==1,(ref,outgoing)
            back=[(i,l) for i,l in links if l.get('nameddest')==link['id']]
            assert len(back)==1,(ref,back)
            bp,bl=back[0]
            assert start-.1<=bp*10000+bl['from'].y0<end,(row,bl,start,end)
            return_page,return_y=destination(link['id'])
            assert return_page==ref_page and abs(return_y-ref_rect.y0)<8,(ref,return_page,return_y,ref_rect)
            # Superscript labels are on the same line and exact target, including repeats.
            clip=pymupdf.Rect(ref_rect.x1-2,ref_rect.y0-8,ref_rect.x1+100,ref_rect.y1)
            label=document[ref_page].get_textbox(clip).strip()
            assert row['label'] in label,(ref,label,row['label'])
            records.append(dict(reference=ref['text'],label=label,from_page=ref_page+1,
                                target_page=target_page+1,return_page=return_page+1))
    expected_images=sum(not row['lost'] for row in reference['images'])
    assert sum(len(p.get_image_info()) for p in document)==expected_images
    return records


def previous_pipeline(root, source, reference):
    """Publish with the actual EX-08 planning code, upgrade a copy, preserve v2."""
    from zipfile import ZipFile
    from tests.common.delivery_support import subprocess_environment
    root.mkdir()
    repository=Path(__file__).resolve().parents[2]
    revision='a35e5dae8332dc9270373b79e85bc760673d9e5f'
    archive=root/'docx-v2-source.zip'
    command=['git','archive','--format=zip','--output='+str(archive),revision]
    result=subprocess.run(command,cwd=repository,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    checkout=root/'source'
    with ZipFile(archive) as package:
        package.extractall(checkout)
    script='''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from src.application.convert import ConversionRequest,convert
assert Path(sys.modules['src.application.convert'].__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
r=convert(ConversionRequest(Path(sys.argv[2]),Path(sys.argv[3]),('markdown','html')))
Path(sys.argv[4]).write_text(json.dumps(dict(job_id=r.job_id,output=str(r.output_dir),status=r.status)),encoding='utf-8')
'''
    original=root/'original-data'
    command=[sys.executable,'-X','utf8','-c',script,str(checkout),str(source),str(original),str(root/'published.json')]
    process=subprocess.run(command,cwd=root,env=subprocess_environment(),capture_output=True,text=True,timeout=120)
    save(root/'process.json',dict(command=command,returncode=process.returncode,stdout=process.stdout,stderr=process.stderr,
                                 source_revision=revision,source_archive_sha256=digest(archive)))
    assert process.returncode==0,process.stderr
    baseline=file_hashes(original)
    data=root/'upgrade-copy'
    shutil.copytree(original,data)
    row=json.loads((root/'published.json').read_text())
    old_export=published(data,row['job_id'])
    old_book=json.loads((old_export/'book.json').read_text())
    assert old_book['pipeline_version']=='docx-dom-v2'
    files=file_hashes(data)
    files.pop('catalog.sqlite')
    old_rows=database(data)
    records=[]
    with api(data) as client:
        old_download=client.get('/api/download/'+row['job_id']).content
        for output in ('md_single','md_chapters','html','pdf'):
            response=client.post('/api/convert',json={'task_id':row['job_id'],'output_format':output})
            assert response.status_code==200,response.text
            task_id=response.json()['task_id']
            export=published(data,task_id)
            reused=json.loads((export/'book.json').read_text())
            assert reused==old_book, '旧 v2 修订被重新解释或补造定位'
            assert JobRuntime(data).get(task_id).selected_revision_id==old_book['revision_id']
            assert client.get('/api/download/'+row['job_id']).content==old_download
            records.append(dict(output=output,job_id=task_id,revision_id=reused['revision_id']))
        fresh=submit(client,source,'html')
        assert fresh['status']=='completed',fresh
        current=published(data,fresh['task_id'])
        new_book=json.loads((current/'book.json').read_text())
        assert new_book['pipeline_version']=='docx-dom-v3'
        assert new_book['revision_id']!=old_book['revision_id']
        assert new_book['source_sha256']==old_book['source_sha256']
        observation,failures=check_export(current,reference)
        assert not failures,failures
        assert client.get('/api/download/'+row['job_id']).content==old_download
    after=file_hashes(data)
    assert all(after.get(name)==sha for name,sha in files.items())
    after_rows=database(data)
    assert all(old in after_rows[table] for table,rows in old_rows.items() for old in rows)
    assert file_hashes(original)==baseline
    save(root/'boundary-results.json',dict(old_status=row['status'],old_book=old_book,old_files=files,
        original_baseline=baseline,old_rows=old_rows,new_rows=after_rows,reexports=records,
        new_revision=new_book['revision_id'],new_observation=observation))
    return current


def run(workdir: Path):
    reference=samples(workdir/'inputs')
    records,failures,exports=[],[],[]
    for name,expected in reference.items():
        record={'example':name}
        try:
            result=convert(ConversionRequest(workdir/'inputs'/(name+'.docx'),workdir/(name+'-data'),('markdown','html','pdf')))
            record.update(status=result.status,job_id=result.job_id,output=str(result.output_dir))
            exports.append(result.output_dir)
            expect_status='partial' if name=='lost-image' else 'completed'
            if result.status!=expect_status:
                failures.append({'example':name,'status':result.status,'expected_status':expect_status})
            observations,errors=check_export(result.output_dir,expected)
            record.update(observations=observations,failures=errors)
            failures.extend({'example':name,'detail':e} for e in errors)
        except Exception:
            record['error']=traceback.format_exc()
            failures.append({'example':name,'error':record['error']})
        records.append(record)
        save(workdir/'combination-results.json',dict(records=records,failures=failures))
    # Public API and persistent history use the SAME independent notes reference.
    try:
        data=workdir/'notes-data'
        first=records[2]
        old=Path(first['output'])
        before=file_hashes(old)
        old_rows=database(data)
        runtime=JobRuntime(data)
        source_path=runtime.source_path(runtime.get(first['job_id']))
        source_sha=digest(source_path)
        with api(data) as client:
            download=client.get('/api/download/'+first['job_id']).content
            for output in ('md_single','md_chapters','html','pdf'):
                response=client.post('/api/convert',json={'task_id':first['job_id'],'output_format':output})
                assert response.status_code==200,response.text
                task_id=response.json()['task_id']
                state=client.get('/api/status/'+task_id).json()
                assert state['status']=='completed',state
                export=published(data,task_id)
                exports.append(export)
                observations,errors=check_export(export,reference['notes'])
                failures.extend({'api_output':output,'detail':error} for error in errors)
                records.append({'api_output':output,'state':state,'output':str(export),'observations':observations,'failures':errors})
                assert file_hashes(old)==before and client.get('/api/download/'+first['job_id']).content==download
                assert digest(source_path)==source_sha
            # A fresh real submission must select the current reader, retaining all old rows.
            fresh=submit(client,workdir/'inputs/notes.docx','html')
            assert fresh['status']=='completed',fresh
            assert file_hashes(old)==before
            assert client.get('/api/download/'+first['job_id']).content==download
        after=database(data)
        assert all(row in after[table] for table,rows in old_rows.items() for row in rows)
        save(workdir/'history.json',dict(files=before,source_sha256=source_sha,database_before=old_rows,database_after=after))
    except Exception:
        failures.append({'history_api':traceback.format_exc()})
    try:
        exports.append(previous_pipeline(workdir/'previous-pipeline',workdir/'inputs/notes.docx',reference['notes']))
    except Exception:
        failures.append({'previous_pipeline':traceback.format_exc()})
    save(workdir/'combination-results.json',dict(records=records,failures=failures))
    required=['逐项阅读三个 Examples 的编号类型、起始值、实例、重启、嵌套交替和返回上层',
              '逐引用核对三类注释正文、重复标签、跨章目标及全部返回位置，逐图核对归属和内部出现',
              '逐页阅读实际 PDF 的编号、层级、引用、图片和损失范围，无缺字、溢出或截断']
    sources=[workdir/'inputs'/(name+'.docx') for name in reference]
    save(workdir/'reading-requirements.json',visual_identity('UC-06-S4',sources,exports,required))
    assert not failures,failures
    checks=['完整原三个 Examples 经真实 Markdown / HTML / PDF 和适用分章转换逐项语义核对',
            '三类注释同形/重复图片逐次定位，未引用内容排除及实际图片损失精确诊断',
            'API 重导出、主动提交、原件、旧修订与历史下载保持']
    require_visual_evidence('UC-06-S4',sources,exports,required,technical_checks=checks)
    return {'checks':checks}


if __name__=='__main__':
    from tests.common.support import run_one
    raise SystemExit(run_one(__file__))
