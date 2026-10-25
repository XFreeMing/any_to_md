"""UC-03-S7：同一任务的重复操作不产生第二个执行者（E4 / US-04）。

作为工具使用者，我希望重复操作被明确拒绝，以便原任务继续且只发布一份结果。
Given 真实 HTTP 上传的同一 job_id 已排队或正在转换，保留早期成功资料。
When 重复 HTTP 转换、独立进程经完整转换入口领取，或另一服务运行时恢复。
Then 请求 / 领取冲突明确，有效租约被跳过；旧快照遇真实续约时 CAS 拒绝。
And 原执行者完成，唯一发布、下载、正文与资源正确，历史资料保持不变。

Examples 分为 queued、processing、有效租约恢复、读取旧租约后真实续约四窗口。
"""

if __name__ == '__main__' and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'common' / 'bootstrap.py'))

import hashlib
import time
import traceback
from pathlib import Path

from src.application.convert import ConversionRequest, convert
from tests.common.delivery_support import extract_download, fixture_cases
from tests.common.inspection import inspect_output
from tests.common.ownership_support import Child, job_row, save, snapshot, wait_until


def check_window(directory: Path, source: Path, case: dict, window: str) -> None:
    directory.mkdir(parents=True)
    data = directory / 'data'
    previous = convert(ConversionRequest(source, data, ('markdown', 'html')))
    before = snapshot(data)
    evidence = {'window': window, 'previous_job_id': previous.job_id, 'before': before}
    children = []

    def capture(name):
        evidence[name] = snapshot(data)
        save(directory / 'evidence.json', evidence)
        return evidence[name]

    def start(role, task=None):
        child = Child(directory / role, data, role, window, task)
        children.append(child)
        evidence[role + '_process'] = child.record
        save(directory / 'evidence.json', evidence)
        return child

    try:
        owner = start('owner')
        owner.ready()
        upload = owner.request('POST', '/api/upload', files={'file': (source.name, source.read_bytes(), 'application/epub+zip')})
        assert upload.status_code == 200, upload.text
        task = upload.json()['task_id']
        evidence['job_id'] = task
        payload = {'task_id': task, 'output_format': 'md_single'}
        accepted = owner.request('POST', '/api/convert', json=payload)
        evidence['accepted'] = {'status': accepted.status_code, 'body': accepted.json()}
        assert accepted.status_code == 200 and accepted.json() == {'task_id': task, 'status': 'queued'}
        paused = owner.event('paused')
        evidence['paused'] = paused
        paused_state = capture('paused_state')
        current = job_row(data, task)
        assert paused['job_id'] == task and paused['pid'] == owner.pid
        assert current['owner_pid'] == owner.pid and current['lease_expires'] > time.time()
        status = owner.request('GET', '/api/status/' + task)
        assert status.status_code == 200 and status.json()['task_id'] == task
        assert status.json()['status'] == ('queued' if window == 'queued' else 'processing')
        assert status.json()['download_url'] is None
        assert owner.request('GET', '/api/download/' + task).status_code == 404
        if window == 'queued':
            assert current['status'] == 'queued' and current['owner_token'] is None
            assert not owner.events('claim-attempt'), 'queued 暂停已越过实际领取边界'
            assert not [row for row in paused_state['database']['exports'] if row['job_id'] == task]
        else:
            assert current['status'] == 'processing' and current['owner_token'] == paused['token']
            assert owner.events('claimed')[0]['token'] == current['owner_token']
            exports = [row for row in paused_state['database']['exports'] if row['job_id'] == task]
            assert len(exports) == 1 and exports[0]['export_id'] == paused['export_id']
            assert exports[0]['status'] == 'staging'

        if window in ('queued', 'processing'):
            duplicate = owner.request('POST', '/api/convert', json=payload)
            evidence['duplicate'] = {'status': duplicate.status_code, 'body': duplicate.json()}
            assert duplicate.status_code == 409
            assert '不可重复排队' in duplicate.json()['detail'] and task in duplicate.json()['detail']
        if window == 'processing':
            competitor = start('competitor', task)
            assert competitor.finish() == 0
            conflict = competitor.event('conversion-conflict')
            claim = competitor.event('claim-conflict')
            evidence['competitor_conflict'] = conflict
            assert conflict['job_id'] == task and '已被领取或已结束' in conflict['error']
            assert claim['job_id'] == task and claim['pid'] != owner.pid
            assert claim['token'] != current['owner_token']
            assert claim['row']['owner_token'] == current['owner_token']
            assert not competitor.events('claimed') and not competitor.events('unexpected-conversion')

        if window in ('valid-lease', 'renewal'):
            recovery = start('recovery')
            observed = recovery.event('recovery-snapshot')
            evidence['recovery_snapshot'] = observed
            assert recovery.pid != owner.pid
            assert len(observed['rows']) == 1 and observed['rows'][0]['job_id'] == task
            old = observed['rows'][0]
            assert old['owner_pid'] == owner.pid and old['lease_expires'] > observed['time']
            if window == 'renewal':
                def renewed_after_snapshot():
                    return next((item for item in owner.events('renewed')
                                 if item['started_at'] > observed['time']
                                 and item['before']['lease_expires'] == old['lease_expires']
                                 and item['started_at'] < old['lease_expires']), None)
                renewed = wait_until(renewed_after_snapshot, 'owner 未在旧租约有效时真实续约', 10)
                evidence['renewal'] = renewed
                assert renewed['token'] == current['owner_token']
                assert renewed['after']['lease_expires'] == renewed['requested_lease'] > old['lease_expires']
                assert renewed['time'] < old['lease_expires'], '续约事务完成时旧租约已过期'
                wait_until(lambda: time.time() > old['lease_expires'] + 0.1, '旧快照租约未到期', 10)
                evidence['before_cas_release'] = {'time': time.time(), 'row': job_row(data, task)}
                assert evidence['before_cas_release']['row']['lease_expires'] > time.time()
                save(directory / 'evidence.json', evidence)
                recovery.release('recover')
                cas = recovery.event('cas-result')
                evidence['cas'] = cas
                assert cas['job_id'] == task and cas['expected_pid'] == old['owner_pid']
                assert cas['expected_lease'] == old['lease_expires'] < cas['started_at']
                assert cas['before']['lease_expires'] > cas['started_at']
                assert cas['before']['owner_token'] == current['owner_token']
                assert cas['result'] is False and cas['after'] == cas['before']
            recovery.ready()
            recovered = recovery.event('recovered')
            assert recovered['count'] == 0
            if window == 'valid-lease':
                assert recovered['time'] < old['lease_expires'], '没有命中有效租约提前跳过'
                assert not recovery.events('cas-result')
            recovered_status = recovery.request('GET', '/api/status/' + task)
            assert recovered_status.status_code == 200 and recovered_status.json()['status'] == 'processing'
            assert recovery.request('GET', '/api/download/' + task).status_code == 404
            recovery.close()
            assert recovery.process.returncode == 0 and owner.process.poll() is None

        after_conflict = capture('after_conflict')
        remaining = job_row(data, task)
        assert remaining['status'] == current['status']
        assert remaining['owner_token'] == current['owner_token'] and remaining['owner_pid'] == current['owner_pid']
        assert remaining['export_id'] == current['export_id']
        assert after_conflict['database']['exports'] == paused_state['database']['exports']
        assert after_conflict['database']['artifacts'] == paused_state['database']['artifacts']
        assert after_conflict['hashes'] == paused_state['hashes'], '拒绝路径改写或增加文件'
        owner.release('convert')

        def terminal():
            response = owner.request('GET', '/api/status/' + task)
            assert response.status_code == 200
            state = response.json()
            return state if state['status'] not in ('queued', 'processing') else None
        completed = wait_until(terminal, '原执行者没有完成', 30)
        evidence['completed_http'] = completed
        assert completed['status'] in ('completed', 'completed_with_warnings'), completed
        assert completed['task_id'] == task and completed['download_url'] == '/api/download/' + task
        download = owner.request('GET', completed['download_url'])
        assert download.status_code == 200
        (directory / 'download.zip').write_bytes(download.content)
        evidence['download_sha256'] = hashlib.sha256(download.content).hexdigest()
        moved = extract_download(download.content, directory / 'downloaded', 'book.md')
        inspection = inspect_output([moved / 'book.md'], case, moved)
        evidence['inspection'] = inspection
        assert inspection['passed'], inspection['failures']
        earlier = owner.request('GET', '/api/download/' + previous.job_id)
        assert earlier.status_code == 200
        (directory / 'previous-download.zip').write_bytes(earlier.content)
        old_job = next(row for row in before['database']['jobs'] if row['job_id'] == previous.job_id)
        assert hashlib.sha256(earlier.content).hexdigest() == before['hashes']['exports'][
            f'{previous.export_id}/{old_job["download_relative"]}']
        final = capture('final')
        job = job_row(data, task)
        exports = [row for row in final['database']['exports'] if row['job_id'] == task]
        assert len(exports) == 1 and exports[0]['export_id'] == job['export_id']
        if window != 'queued':
            assert job['export_id'] == paused['export_id']
        assert exports[0]['status'] == job['status'] == completed['status']
        assert job['owner_token'] == owner.events('claimed')[0]['token']
        assert job['lease_expires'] is None and job['download_relative']
        artifacts = [row for row in final['database']['artifacts'] if row['export_id'] == job['export_id']]
        downloads = [row for row in artifacts if row['relative_path'] == job['download_relative']]
        assert len(downloads) == 1 and downloads[0]['sha256'] == evidence['download_sha256']
        for artifact in artifacts:
            path = data / job['storage_key'] / artifact['relative_path']
            assert path.stat().st_size == artifact['size_bytes']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
        assert len(final['database']['jobs']) == len(before['database']['jobs']) + 1, '重复操作创建了另一任务'
        assert len(final['database']['exports']) == len(before['database']['exports']) + 1
        assert not final['hashes']['staging'] and not final['hashes']['jobs']
        assert final['hashes']['sources'] == before['hashes']['sources']
        assert final['hashes']['revisions'] == before['hashes']['revisions']
        for name, digest in before['hashes']['exports'].items():
            assert final['hashes']['exports'][name] == digest
        for table in ('sources', 'revisions'):
            assert final['database'][table] == before['database'][table]
        assert next(row for row in final['database']['jobs'] if row['job_id'] == previous.job_id) == old_job
        assert owner.process.poll() is None
        owner.close()
        assert owner.process.returncode == 0
        evidence['result'] = 'passed'
    except Exception:
        evidence['failure'] = traceback.format_exc()
        raise
    finally:
        for child in reversed(children):
            child.close()
        evidence['processes'] = [child.record for child in children]
        save(directory / 'evidence.json', evidence)


def run(workdir: Path) -> dict:
    source, case = next(pair for pair in fixture_cases(workdir / 'inputs') if pair[1]['id'] == 'epub-structured')
    failures = {}
    for window in ('queued', 'processing', 'valid-lease', 'renewal'):
        try:
            check_window(workdir / window, source, case, window)
        except Exception:
            failures[window] = traceback.format_exc()
    save(workdir / 'windows.json', {'failures': failures})
    assert not failures, failures
    return {'checks': ['queued 同 job_id 重复 HTTP 转换 409，首次调度继续完成',
                       'processing 重复 HTTP 409，独立完整转换入口领取冲突且所有者保持',
                       '独立实际服务启动恢复跳过有效租约，不终止原执行者',
                       '恢复读取旧快照后 owner 在有效期真实续约，旧快照到期后实际 CAS 返回 false',
                       '四窗口原执行者正常退出、唯一发布下载及正文资源正确、历史资料哈希不变']}


if __name__ == '__main__':
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
