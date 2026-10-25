"""UC-08-S2 · E5 / US-12: 已发布正文经现有 API 换输出，身份和内容均可核对。

Given 四来源的独立标题路径、直接正文与逐次图片位置参考，已发布修订。
When 对终态任务选择 Markdown / HTML / PDF 及适用分章，原件离线且禁止重新读取。
Then 复用准确修订，保留结构、正文、图文、诊断和历史下载；逐组合保留失败。
旧任务选择旧修订、主动新提交、失败重试、schema-1 原件重建作为最小守卫。
"""

if __name__ == '__main__' and not __package__:
    import runpy
    from pathlib import Path
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'common' / 'bootstrap.py'))

import importlib
import json
import shutil
import sqlite3
import traceback
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.application.convert import ConversionRequest, convert
from src.application.jobs import JobRuntime
from tests.common.conversion_support import api, submit, published, file_hashes
from tests.common.delivery_support import source_server, require_visual_evidence
from tests.common.reuse_support import sources, save, digest, database, observe, canonical_observation, view_observation, TEXT


def guards(root, source):
    root.mkdir()
    data = root / 'data'
    runtime = JobRuntime(data)
    original = convert(ConversionRequest(source, data, ('html',)))
    first = json.loads((original.output_dir / 'book.json').read_text(encoding='utf-8'))
    before = file_hashes(original.output_dir)
    # An actual second Reader execution produces another published revision for
    # the SAME source; the version perturbation is explicitly an identity guard.
    module = importlib.import_module('src.readers.html.reader')
    public = importlib.import_module('src.readers.html')
    with patch.object(module, 'PIPELINE_VERSION', module.PIPELINE_VERSION+'-identity-guard'), patch.object(public, 'PIPELINE_VERSION', public.PIPELINE_VERSION+'-identity-guard'), observe(data) as calls:
        newer = convert(ConversionRequest(source, data, ('html',)))
    assert any(call['kind'] == 'reader' for call in calls), calls
    assert newer.revision_id != original.revision_id
    with observe(data, forbid=True) as reuse_calls:
        selected = runtime.queue(original.job_id, 'md_single')
        selected_id = selected.job_id
        assert JobRuntime(data).get(selected_id).selected_revision_id == original.revision_id, '任务重开丢失修订选择'
        result = convert(ConversionRequest(data_dir=data, job_id=selected_id))
    assert result.revision_id == original.revision_id, '按 source_id 取了最新修订'
    # Same active submission remains a fresh interpretation, not a cache lookup.
    with observe(data) as active_calls:
        active = convert(ConversionRequest(source, data, ('html',)))
    assert any(call['kind'] == 'reader' for call in active_calls)
    assert active.revision_id == original.revision_id
    failed = runtime.upload('broken.epub', b'not a zip', 'epub')
    from src.application.jobs import ConversionFailure
    try:
        convert(ConversionRequest(data_dir=data, job_id=failed.job_id))
    except ConversionFailure:
        pass
    assert runtime.get(failed.job_id).status.value == 'failed'
    retry = runtime.queue(failed.job_id, 'html')
    with observe(data) as retry_calls:
        try:
            convert(ConversionRequest(data_dir=data, job_id=retry.job_id))
        except ConversionFailure:
            pass
    assert any(call['kind'] == 'reader' for call in retry_calls)
    assert runtime.get(retry.job_id).status.value == 'failed'
    # Genuine schema-1 representation has no canonical fragments. Hand-author
    # that missing body boundary in an isolated retained copy; do not downgrade DDL.
    legacy = root / 'schema1-copy'
    shutil.copytree(data, legacy)
    rt = JobRuntime(legacy)
    with closing(sqlite3.connect(legacy / 'catalog.sqlite')) as connection:
        key = connection.execute('SELECT content_storage_key FROM revisions WHERE revision_id=?', (original.revision_id,)).fetchone()[0]
        path = rt.storage.resolve(key)
        snapshot = dict(first, schema_version=1)
        old_id = str(__import__('uuid').uuid4())
        snapshot['revision_id'] = old_id
        snapshot['pipeline_version'] = 'schema1-historical-reader'
        for name in ('content', 'styles', 'anchor_owners'):
            snapshot.pop(name)
        destination = legacy/'revisions'/old_id
        path.parent.rename(destination)
        path = destination/'book.json'
        save(path, snapshot)
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('UPDATE revisions SET revision_id=?,pipeline_version=?,content_storage_key=?,content_sha256=? WHERE revision_id=?',
                           (old_id,snapshot['pipeline_version'],path.relative_to(legacy).as_posix(),digest(path),original.revision_id))
        for table in ('exports','section_versions'):
            connection.execute(f'UPDATE {table} SET revision_id=? WHERE revision_id=?',(old_id,original.revision_id))
        connection.execute('UPDATE jobs SET selected_revision_id=? WHERE selected_revision_id=?',(old_id,original.revision_id))
        connection.commit()
    legacy_before = file_hashes(legacy)
    legacy_before.pop('catalog.sqlite')
    with observe(legacy) as legacy_calls:
        job = rt.queue(original.job_id, 'md_single')
        rebuilt = convert(ConversionRequest(data_dir=legacy, job_id=job.job_id))
    assert any(call['kind'] == 'reader' for call in legacy_calls), 'schema-1 不能假装有共同正文'
    assert json.loads((rebuilt.output_dir/'book.json').read_text())['schema_version'] == 2
    legacy_after = file_hashes(legacy)
    assert all(legacy_after.get(name)==sha for name,sha in legacy_before.items()), '原件重建改写旧资料'
    assert any(row['old_revision_id']==old_id and row['new_revision_id']==rebuilt.revision_id
               for row in database(legacy)['revision_migrations']), '原件重建缺迁移谱系'
    assert file_hashes(original.output_dir) == before
    from src.adapters.html_source import pack_html_source
    partial = runtime.upload('partial.zip',pack_html_source('partial.html',b'<h1>Retained</h1><p>Readable body.</p><img src="missing.png"/>'),'html')
    queued = runtime.queue(partial.job_id,'html')
    partial_first = convert(ConversionRequest(data_dir=data,job_id=queued.job_id))
    assert partial_first.status == 'partial'
    with observe(data,forbid=True) as partial_calls:
        partial_job = runtime.queue(partial_first.job_id,'md_single')
        partial_second = convert(ConversionRequest(data_dir=data,job_id=partial_job.job_id))
    assert partial_second.status == 'partial' and partial_first.revision_id == partial_second.revision_id
    partial_reports = [json.loads((r.output_dir/'report.json').read_text(encoding='utf-8')) for r in (partial_first,partial_second)]
    assert partial_reports[0]['diagnostics'] == partial_reports[1]['diagnostics']
    save(root/'evidence.json', {'original': original.__dict__ | {'output_dir': str(original.output_dir), 'report_path': str(original.report_path)}, 'newer_revision': newer.revision_id, 'selected_revision': result.revision_id,
                             'calls': calls, 'reuse_calls': reuse_calls, 'active_calls': active_calls, 'retry_calls': retry_calls, 'legacy_calls': legacy_calls, 'partial_calls':partial_calls,'partial_reports':partial_reports,
                             'database': database(data), 'schema1_database': database(legacy), 'historical_files': before})


def run(workdir: Path):
    reference = sources(workdir/'inputs')
    records, failures, review_exports, review_sources = [], [], [], []
    with source_server(workdir/'inputs') as base:
        reference['source_documents']['url'] = base+'/journey.html'
        save(workdir/'inputs'/'independent-reference.json', reference)
        for kind, filename in [('epub','journey.epub'), ('html','journey.zip'), ('docx','journey.docx'), ('url','journey.url')]:
            source = workdir/'inputs'/filename
            if kind == 'url':
                source.write_text(base+'/journey.html', encoding='utf-8')
            review_sources.append(source)
            data = workdir/kind/'data'
            with api(data) as client:
                first = submit(client, source, 'html')
                save(workdir/kind/'first-state.json', first)
                if first['status'] not in {'completed','completed_with_warnings','partial'}:
                    failures.append({'kind':kind,'first':first})
                    continue
                old = published(data, first['task_id'])
                review_exports.append(old)
                snapshot = json.loads((old/'book.json').read_text(encoding='utf-8'))
                before = file_hashes(old)
                old_download = client.get(first['download_url']).content
                original_observed, original_failures = canonical_observation(old, reference, kind)
                original_view, original_view_failures = view_observation([old/'book.html'], old, reference, kind)
                records.append({'kind':kind, 'stage':'first', 'canonical':original_observed, 'view':original_view, 'failures':original_failures+original_view_failures})
                failures.extend({'kind':kind, 'stage':'first', 'content': f} for f in original_failures+original_view_failures)
                # Independently exercise the original Markdown/PDF Writers before
                # taking originals offline. Content errors are first-conversion
                # errors as well; comparison of two equally wrong outputs is not proof.
                for initial_output in ('md_single','pdf'):
                    initial_record = {'kind':kind,'stage':'first-'+initial_output}
                    try:
                        initial = submit(client,source,initial_output)
                        initial_record['state'] = initial
                        assert initial['status'] in {'completed','completed_with_warnings','partial'}, initial
                        initial_export = published(data,initial['task_id'])
                        review_exports.append(initial_export)
                        initial_views = [[initial_export/('book.md' if initial_output=='md_single' else 'book.html')]]
                        if initial_output=='md_single' and kind!='url':
                            initial_views.append(sorted((initial_export/'chapters').glob('*.md')))
                        observed_views = [view_observation(paths,initial_export,reference,kind) for paths in initial_views]
                        initial_record['views'] = observed_views
                        initial_record['files'] = file_hashes(initial_export)
                        initial_record['failures'] = [failure for _,problems in observed_views for failure in problems]
                        failures.extend({'kind':kind,'stage':'first-'+initial_output,'content':f} for f in initial_record['failures'])
                        initial_record['status'] = 'failed' if initial_record['failures'] else 'technical_pass'
                    except Exception:
                        initial_record.update(status='failed',error=traceback.format_exc())
                        failures.append(initial_record.copy())
                    records.append(initial_record)
                # Keep captured source intact in evidence but make it unavailable
                # at the actual original path. Every output remains independent.
                source_dir = data/'sources'
                # Antivirus/indexers can briefly lock a Windows directory after
                # PDF conversion. Move retained files with bounded retries;
                # an environment failure must not suppress the output matrix.
                import gc
                import time
                offline = data/'offline-originals'
                offline.mkdir()
                offline_failures = []
                for original_file in source_dir.iterdir():
                    for attempt in range(8):
                        try:
                            original_file.rename(offline/original_file.name)
                            break
                        except PermissionError:
                            gc.collect()
                            if attempt==7:
                                offline_failures.append(str(original_file))
                            else:
                                time.sleep(.2)
                if offline_failures:
                    failures.append({'kind':kind,'offline_setup_failed':offline_failures})
                outputs = ['md_single', 'html', 'pdf'] + (['md_chapters'] if kind != 'url' else [])
                for output in outputs:
                    record = {'kind':kind,'output':output, 'original_job':first['task_id'], 'expected_revision':snapshot['revision_id']}
                    calls = []
                    try:
                        with observe(data, forbid=True) as calls:
                            response = client.post('/api/convert', json={'task_id':first['task_id'],'output_format':output})
                        record['http'] = response.status_code
                        assert response.status_code == 200, response.text
                        state = client.get('/api/status/'+response.json()['task_id']).json()
                        record['state'] = state
                        assert state['status'] in {'completed','completed_with_warnings','partial'}, state
                        new = published(data, state['task_id'])
                        review_exports.append(new)
                        book = json.loads((new/'book.json').read_text(encoding='utf-8'))
                        assert book == snapshot, '重导出改变正文、身份、诊断或资源'
                        assert state['task_id'] != first['task_id'] and new != old
                        canonical, content_failures = canonical_observation(new, reference, kind)
                        paths = sorted((new/'chapters').glob('*.md')) if output == 'md_chapters' else [new/('book.md' if output=='md_single' else 'book.html')]
                        view, view_failures = view_observation(paths, new, reference, kind)
                        record.update(canonical=canonical, view=view, content_failures=content_failures+view_failures)
                        failures.extend({'kind':kind, 'output':output, 'content': f} for f in content_failures+view_failures)
                        if output=='pdf':
                            import pymupdf
                            with pymupdf.open(new/'book.pdf') as pdf:
                                text = '\n'.join(page.get_text() for page in pdf)
                                assert all(value in text for _, value in TEXT), text
                                assert sum(len(page.get_images()) for page in pdf) >= 2
                                record['pdf_pages'] = len(pdf)
                        download = client.get(state['download_url'])
                        assert download.status_code == 200
                        record['download_sha256'] = __import__('hashlib').sha256(download.content).hexdigest()
                        record['files'] = file_hashes(new)
                        record['status'] = 'failed' if content_failures+view_failures else 'technical_pass'
                    except Exception:
                        record.update(status='failed', error=traceback.format_exc())
                        failures.append({'kind':kind, 'output':output, 'error':record['error']})
                    finally:
                        record['calls'] = calls
                        record['database'] = database(data)
                        assert file_hashes(old) == before
                        assert client.get(first['download_url']).content == old_download
                        records.append(record)
                        save(workdir/'combination-results.json', {'records':records,'failures':failures})
                assert all(event['kind']=='file' for record in records if record.get('stage') != 'first' for event in record.get('calls',[])), '重导出调用外部边界'
    try:
        guards(workdir/'identity-guards', workdir/'inputs'/'journey.zip')
    except Exception:
        failures.append({'identity_guards':traceback.format_exc()})
    save(workdir/'combination-results.json', {'records':records,'failures':failures})
    from tests.common.delivery_support import visual_identity
    save(workdir/'reading-requirements.json',visual_identity('UC-08-S2',review_sources,review_exports,
         ['逐来源阅读首次及重导出的整体和适用分章，标题路径、正文归属与三次图片及图题对应独立源参考',
          '真实 PDF 逐页阅读无缺字、图文错序、溢出或分页截断']))
    assert not failures, failures
    checks = ['四来源全部输出逐组合核对独立结构、直接正文、逐次图片容器顺序及源定位',
              '离线正文重导出不读取原件、Reader、OCR 或网络；准确修订绑定及历史身份哈希保持',
              '主动提交、同源多修订、失败重试及 schema-1 原件重建守卫']
    require_visual_evidence('UC-08-S2', review_sources, review_exports,
                            ['逐来源阅读首次及重导出的整体和适用分章，标题路径、正文归属与三次图片及图题对应独立源参考',
                             '真实 PDF 逐页阅读无缺字、图文错序、溢出或分页截断'], technical_checks=checks)
    return {'checks':checks}


if __name__ == '__main__':
    from tests.common.support import run_one
    raise SystemExit(run_one(__file__))
