"""UC-08-S4 · E5 / US-12 / US-05: 三类持久资料在实际读取边界拒绝损坏。

Given 独立副本的原件 / 捕获包必要资源、正文 JSON / 必要资源、下载分别缺失、
大小变化、等长篡改。When 首次解析、schema-1 明确原件重建、正文重导出或 HTTP 下载。
Then 准确拒绝，无伪成功下载，其余完好资料、历史文件和行保持。
另以校验和正确但内部无效的受控持久样本验证 schema、身份、结构和安全路径。
"""

if __name__ == '__main__' and not __package__:
    import runpy
    from pathlib import Path
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'common' / 'bootstrap.py'))

import io
import json
import shutil
import sqlite3
import traceback
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile, ZIP_STORED

from src.application.convert import ConversionRequest, convert
from src.application.jobs import JobRuntime
from tests.common.conversion_support import api, submit, file_hashes
from tests.common.reuse_support import sources, save, digest, database, observe


def legacy_body(data, revision):
    runtime = JobRuntime(data)
    with closing(sqlite3.connect(data/'catalog.sqlite')) as connection:
        key = connection.execute('SELECT content_storage_key FROM revisions WHERE revision_id=?', (revision,)).fetchone()[0]
        path = runtime.storage.resolve(key)
        book = json.loads(path.read_text(encoding='utf-8'))
        book['schema_version'] = 1
        for key in ('content','styles','anchor_owners'):
            book.pop(key)
        save(path, book)
        connection.execute('UPDATE revisions SET content_sha256=? WHERE revision_id=?', (digest(path),revision))
        connection.commit()


def mutate(path, mode, member=False):
    before = {'sha256':digest(path),'size':path.stat().st_size}
    content = path.read_bytes()
    if member:
        with ZipFile(io.BytesIO(content)) as archive:
            members = {n:archive.read(n) for n in archive.namelist()}
        name = 'red.png'
        if mode == 'missing':
            members.pop(name)
        elif mode == 'size':
            members[name] += b'changed'
        else:
            value = members[name]
            members[name] = value[:len(value)//2]+bytes([value[len(value)//2]^1])+value[len(value)//2+1:]
        stream = io.BytesIO()
        # Captured HTML source uses ZIP_STORED; same-length member mutation
        # also updates the real ZIP CRC without changing archive byte length.
        with ZipFile(stream,'w',compression=ZIP_STORED) as archive:
            with ZipFile(io.BytesIO(content)) as original:
                for item in original.infolist():
                    if item.filename in members:
                        archive.writestr(item,members[item.filename])
        path.write_bytes(stream.getvalue())
    elif mode == 'missing':
        path.unlink()
    elif mode == 'size':
        path.write_bytes(content+b'changed')
    else:
        index = len(content)//2
        path.write_bytes(content[:index]+bytes([content[index]^1])+content[index+1:])
    after = {'sha256':digest(path),'size':path.stat().st_size} if path.exists() else None
    if mode == 'equal':
        assert after['size']==before['size'] and after['sha256']!=before['sha256']
    return {'before':before,'after':after,'member':'red.png' if member else None}


def run(workdir):
    sources(workdir/'inputs')
    baseline = workdir/'baseline'
    with api(baseline) as client:
        first = submit(client,workdir/'inputs'/'journey.zip','md_single')
        control = submit(client,workdir/'inputs'/'journey.zip','html')
    runtime = JobRuntime(baseline)
    old = runtime.get(first['task_id'])
    with closing(sqlite3.connect(baseline/'catalog.sqlite')) as c:
        revision,key = c.execute('SELECT revision_id,content_storage_key FROM revisions').fetchone()
    book = json.loads(runtime.storage.resolve(key).read_text(encoding='utf-8'))
    cases = [(stage,kind,mode) for stage,kind in [('first','source'),('first','source-resource'),('rebuild','source'),('rebuild','source-resource'),('reuse','body'),('reuse','body-resource'),('download','download')]
             for mode in ('missing','size','equal')]
    results, failures = [], []
    for stage,kind,mode in cases:
        directory = workdir/(stage+'-'+kind+'-'+mode)
        data = directory/'data'
        shutil.copytree(baseline,data)
        rt = JobRuntime(data)
        record = {'stage':stage,'object':kind,'mutation':mode}
        calls = []
        try:
            if stage=='rebuild':
                legacy_body(data, revision)
            target = rt.storage.resolve(old.input_key if kind.startswith('source') else key)
            if kind=='body-resource':
                target = target.parent/'assets'/book['assets'][0]['file_name']
            if kind=='download':
                target = rt.download(first['task_id'])
            record['target'] = str(target)
            record['mutation_identity'] = mutate(target,mode,member=kind=='source-resource')
            before = file_hashes(data)
            before.pop('catalog.sqlite')
            before_rows = database(data)
            record['database_before'] = before_rows
            # A separate already-published HTML download stays good in all cases.
            with api(data) as client:
                good = client.get('/api/download/'+control['task_id'])
                assert good.status_code==200
                if stage=='first':
                    # A real pending task already registered this captured source.
                    job = rt.catalog.create_job(__import__('uuid').uuid4().hex,old.filename,'html',(),None,old.source_id)
                    selected = rt.queue(job.job_id,'html')
                    from src.application.jobs import ConversionFailure
                    try:
                        with observe(data) as calls:
                            convert(ConversionRequest(data_dir=data,job_id=selected.job_id))
                    except ConversionFailure:
                        pass
                    state = client.get('/api/status/'+selected.job_id).json()
                elif stage=='download':
                    response = client.get('/api/download/'+first['task_id'])
                    record.update(http=response.status_code, response=response.text)
                    assert response.status_code != 200, '损坏下载被暴露'
                    state = None
                else:
                    with observe(data,forbid=stage=='reuse') as calls:
                        response = client.post('/api/convert',json={'task_id':first['task_id'],'output_format':'html'})
                    assert response.status_code==200,response.text
                    state = client.get('/api/status/'+response.json()['task_id']).json()
                if state:
                    record['state']=state
                    assert state['status']=='failed',state
                    assert not state.get('download_url'),state
                    assert client.get('/api/download/'+state['task_id']).status_code!=200
                    failed = rt.get(state['task_id'])
                    report = json.loads(rt.storage.resolve(failed.report_key).read_text(encoding='utf-8'))
                    record['diagnostic']=report
                    assert any(value in report['error'] for value in ('校验','不存在','损坏','size','SHA')), report
                    assert not any('Forbidden' in e.get('message','') for e in report.get('exception_chain',[])), '由禁止 Reader 的观察器阻断，不代表产品正确拒绝损坏'
                assert client.get('/api/download/'+control['task_id']).content==good.content
            after=file_hashes(data)
            for name,value in before.items():
                assert after.get(name)==value, ('改写既有资料',name)
            after_rows=database(data)
            for table,rows in before_rows.items():
                for row in rows:
                    assert row in after_rows[table], ('改写历史行',table,row)
            record.update(status='passed',database_after=after_rows,hashes_before=before,hashes_after=after)
        except Exception:
            record.update(status='failed',error=traceback.format_exc())
            failures.append({k:record[k] for k in ('stage','object','mutation','error')})
        finally:
            record['calls']=calls
            results.append(record)
            save(directory/'evidence.json',record)
            save(workdir/'combination-results.json',{'cases':results,'failures':failures})
    # These fixtures deliberately carry a matching trusted byte hash: validation
    # must also reject internally invalid snapshots, not just corrupted bytes.
    invalid = {
        'schema':lambda b:b.update(schema_version=999),
        'identity':lambda b:b.update(source_id='other-source'),
        'pipeline':lambda b:b.update(pipeline_version='other-pipeline'),
        'missing-child':lambda b:b['nodes'][0]['children_ids'].append('missing'),
        'cycle':lambda b:b['nodes'][0]['children_ids'].append(b['nodes'][0]['node_id']),
        'owner':lambda b:b['content'][0].update(node_id='missing'),
        'duplicate-fragment':lambda b:b['content'].append(b['content'][0]),
        'missing-content':lambda b:b['nodes'][0]['content_ids'].append('missing'),
        'unsafe-asset':lambda b:b['assets'][0].update(file_name='../outside.png'),
        'unknown-asset':lambda b:b['content'][0].update(html=b['content'][0]['html']+'<img src="asset://'+'0'*64+'"/>'),
        'anchor-owner':lambda b:b['anchor_owners'].update({'forged':'missing'}),
    }
    for label,change in invalid.items():
        directory=workdir/('invalid-'+label)
        data=directory/'data'
        shutil.copytree(baseline,data)
        rt=JobRuntime(data)
        snapshot=json.loads(json.dumps(book))
        change(snapshot)
        save(rt.storage.resolve(key),snapshot)
        with closing(sqlite3.connect(data/'catalog.sqlite')) as connection:
            connection.execute('UPDATE revisions SET content_sha256=? WHERE revision_id=?',(digest(rt.storage.resolve(key)),revision))
            connection.commit()
        record={'fixture':'checksum-valid structurally invalid snapshot','case':label}
        try:
            with api(data) as client, observe(data,forbid=True) as calls:
                response=client.post('/api/convert',json={'task_id':first['task_id'],'output_format':'html'})
                assert response.status_code==200,response.text
                state=client.get('/api/status/'+response.json()['task_id']).json()
                record.update(state=state,calls=calls)
                assert state['status']=='failed' and not state.get('download_url'),state
                job=rt.get(state['task_id'])
                report=json.loads(rt.storage.resolve(job.report_key).read_text(encoding='utf-8'))
                record['diagnostic']=report
                assert 'Forbidden' not in report['error']
            record['status']='passed'
        except Exception:
            record.update(status='failed',error=traceback.format_exc())
            failures.append(record)
        save(directory/'evidence.json',record)
        results.append(record)
    save(workdir/'combination-results.json',{'cases':results,'failures':failures})
    assert not failures,failures
    return {'checks':['21 个独立副本逐项覆盖原件及包内必要资源首次解析/原件重建、正文及必要资源重导出、下载的三种损坏',
                      '11 个校验和正确的无效 schema / 身份 / pipeline / 结构归属 / 路径及资源引用在读取时拒绝',
                      '每例保留修改前后身份、实际诊断、调用观察、数据库全行及其他完好下载哈希']}


if __name__=='__main__':
    from tests.common.support import run_one
    raise SystemExit(run_one(__file__))
