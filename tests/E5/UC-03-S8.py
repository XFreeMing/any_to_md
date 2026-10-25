"""UC-03-S8：启动拒绝不兼容数据库，升级失败后仍可恢复资料（E5 / US-05）。

Given 独立历史 DDL、schema-4 DDL 与手写资料参考构建的隔离副本（不是 S2 真实旧数据）。
When 真实 Web 启动遇未来版本、重建表 / v5 添加修订绑定后异常、最终真实外键校验失败。
Then 明确失败且进程退出；版本、完整结构、全部记录和资料保留，备份可只读恢复。
"""

if __name__ == '__main__' and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'common' / 'bootstrap.py'))

import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

from tests.common.delivery_support import digest, subprocess_environment, tree_hashes


LEGACY_DDL = Path(__file__).resolve().parents[1] / 'common' / 'legacy_catalog_v2.sql'
HISTORY = 'fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4'
STAMP = '2026-01-02T03:04:05+00:00'


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def logical(connection: sqlite3.Connection) -> dict:
    """Observe every schema object, column, index, FK and existing table row."""
    def rows(sql):
        return [[{'blob_hex': cell.hex()} if isinstance(cell, bytes) else cell for cell in row]
                for row in sqlite3.Connection.execute(connection, sql)]

    master = rows('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name')
    tables = [row[1] for row in master if row[0] == 'table']
    indexes = [row[1] for row in master if row[0] == 'index']
    return {'version': sqlite3.Connection.execute(connection, 'PRAGMA user_version').fetchone()[0],
            'sqlite_master': master,
            'tables': {name: {'columns': rows(f'PRAGMA table_xinfo({quote(name)})'),
                              'foreign_keys': rows(f'PRAGMA foreign_key_list({quote(name)})'),
                              'indexes': rows(f'PRAGMA index_list({quote(name)})'),
                              'records': sorted(rows(f'SELECT * FROM {quote(name)}'), key=repr)}
                       for name in tables},
            'indexes': {name: rows(f'PRAGMA index_xinfo({quote(name)})') for name in indexes},
            'integrity': rows('PRAGMA integrity_check'),
            'foreign_key_check': rows('PRAGMA foreign_key_check')}


def identity(path: Path) -> dict:
    return {'path': str(path.resolve()), 'sha256': digest(path), 'size_bytes': path.stat().st_size}


def inspect_database(path: Path) -> dict:
    before = identity(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        state = logical(connection)
    assert identity(path) == before, '只读核验改写了数据库'
    return {'identity': before, 'logical': state, 'open_mode': 'ro/query_only'}


def materials(data: Path) -> dict:
    return {name: tree_hashes(data / name) for name in ('sources', 'revisions', 'exports')}


def build_reference(data: Path) -> dict:
    """Hand-authored old rows/files; never downgrade a current catalog."""
    for name in ('sources', 'revisions', 'exports'):
        (data / name).mkdir(parents=True)
    source = data / 'sources' / 'reference.epub'
    with ZipFile(source, 'w') as archive:
        for name, text in {
            'mimetype': 'application/epub+zip',
            'META-INF/container.xml': '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            'book.opf': '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">s8-reference</dc:identifier><dc:title>保留资料参考</dc:title><dc:language>zh</dc:language></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
            'chapter.xhtml': '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>保留资料参考</title></head><body><h1 id="one">第一章</h1><p>独立参考正文，不由迁移输出生成。</p></body></html>',
        }.items():
            archive.writestr(name, text.encode())
    source_hash = digest(source)
    with closing(sqlite3.connect(data / 'catalog.sqlite')) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(LEGACY_DDL.read_text(encoding='utf-8'))
        connection.executemany('INSERT INTO schema_migrations VALUES(?,?)', [(1, STAMP), (2, STAMP)])
        connection.execute('INSERT INTO books VALUES(?,?,?,?)', ('book-reference', '保留资料参考', '["参考作者"]', STAMP))
        connection.execute('INSERT INTO editions VALUES(?,?,?,?)', ('edition-reference', 'book-reference', source_hash, STAMP))
        connection.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?)',
                           ('source-reference', 'edition-reference', 'epub', source_hash,
                            'sources/reference.epub', 'reference.epub', STAMP))
        connection.execute('INSERT INTO section_identities VALUES(?,?,?,?)',
                           ('node-one', 'edition-reference', 'epub:chapter.xhtml#one', STAMP))
        for number in (1, 2):
            revision, job, export = (f'{kind}-{number}' for kind in ('revision', 'job', 'export'))
            snapshot = {'schema_version': 1, 'book_id': 'book-reference', 'edition_id': 'edition-reference',
                        'source_id': 'source-reference', 'revision_id': revision, 'source_sha256': source_hash,
                        'pipeline_version': f'historical-reference-{number}',
                        'metadata': {'title': '保留资料参考', 'authors': ['参考作者'], 'language': 'zh', 'identifier': None},
                        'roots': ['node-one'], 'reading_order_ids': ['node-one'],
                        'nodes': [{'node_id': 'node-one', 'parent_id': None, 'children_ids': [], 'depth': 1,
                                   'sibling_order': 0, 'ordinal_path': [1], 'title_original': '第一章',
                                   'title_display': '第一章', 'role': 'chapter', 'source_key': 'epub:chapter.xhtml#one',
                                   'source': {'path': 'chapter.xhtml', 'fragment': 'one', 'spine_index': 0},
                                   'continuation_sources': [], 'anchor': 'sec-nodeone', 'has_direct_content': True}],
                        'assets': [], 'diagnostics': []}
            revision_path = data / 'revisions' / (revision + '.json')
            save(revision_path, snapshot)
            revision_hash = digest(revision_path)
            connection.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)',
                               (revision, 'edition-reference', f'revisions/{revision}.json', revision_hash,
                                revision_hash, snapshot['pipeline_version'], 'ready', STAMP))
            connection.execute('INSERT INTO section_versions VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (revision, 'node-one', None, 0, 1, 'chapter', '第一章', '第一章', '1',
                                json.dumps({'primary': snapshot['nodes'][0]['source'], 'continuations': []})))
            connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                               (job, 'source-reference', 'completed', None, STAMP, STAMP))
            connection.execute('INSERT INTO exports VALUES(?,?,?,?,?,?,?,?)',
                               (export, revision, job, '{"view":"both"}', 'completed', f'exports/{export}', STAMP, STAMP))
            output = data / 'exports' / export
            output.mkdir()
            (output / 'book.md').write_text('# 第一章\n\n独立参考正文，不由迁移输出生成。\n', encoding='utf-8')
            with ZipFile(output / 'book.zip', 'w') as archive:
                archive.write(output / 'book.md', 'book.md')
            for path in sorted(output.iterdir()):
                media = 'text/markdown' if path.suffix == '.md' else 'application/zip'
                connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',
                                   (export + ':' + path.name, export, 'single', path.name, digest(path), path.stat().st_size, media))
            connection.execute('INSERT INTO export_locations VALUES(?,?,?,?,?,?)',
                               (export, 'node-one', 'single', '', 'book.md', 'sec-nodeone'))
        connection.commit()
    state = inspect_database(data / 'catalog.sqlite')
    tables = state['logical']['tables']
    assert state['logical']['version'] == 0
    assert 'owner_token' not in [row[1] for row in tables['jobs']['columns']]
    assert next(row for row in tables['jobs']['columns'] if row[1] == 'source_id')[3] == 1
    assert next(row for row in tables['exports']['columns'] if row[1] == 'revision_id')[3] == 1
    assert state['logical']['integrity'] == [['ok']] and not state['logical']['foreign_key_check']
    return {'ddl': identity(LEGACY_DDL), 'historical_commit': HISTORY,
            'historical_path': 'src/any_to_md/adapters/persistence/sqlite.py',
            'version_basis': 'user_version=0 (historical default), schema_migrations=1,2',
            'provenance': 'synthetic, independently authored rows/files; not a real S2 user database',
            'database': state, 'materials': materials(data)}


def startup_child() -> None:
    directory, data, window = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]

    def emit(name, **values):
        event = {'name': name, 'pid': os.getpid(), 'time': time.time(), **values}
        save(directory / (name + '.json'), event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    emit('identity', parent_pid=os.getppid(), executable=sys.executable, python=sys.version,
         platform=platform.platform(), window=window)
    # Observe real SQL at the connection boundary. Never replace _migrate.
    connect = sqlite3.connect

    class Boundary(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            normalized = ' '.join(sql.split()).upper()
            if window in ('validation', 'v4-validation') and normalized == 'PRAGMA FOREIGN_KEY_CHECK':
                version = super().execute('PRAGMA user_version').fetchone()[0]
                if version == 5:
                    # Earlier check in _upgrade_jobs ran normally. Break only
                    # an uncommitted FK now, so SQLite's final check really fails.
                    valid = list(super().execute(sql))
                    super().execute("UPDATE exports SET revision_id='injected-missing-revision' WHERE export_id='export-2'")
                    violations = [list(row) for row in super().execute(sql)]
                    emit('injected', boundary='final-foreign-key-check', in_transaction=self.in_transaction,
                         version=version, valid_before=valid, violations=violations,
                         transaction=logical(self), backups=[identity(p) for p in data.glob('*.before-v5.*.sqlite')])
            cursor = super().execute(sql, *args, **kwargs)
            if ((window == 'ddl' and normalized == 'ALTER TABLE JOBS_V3 RENAME TO JOBS')
                    or (window == 'v4-ddl' and normalized.startswith('ALTER TABLE JOBS ADD COLUMN SELECTED_REVISION_ID'))):
                emit('injected', boundary='after-real-jobs-rebuild', in_transaction=self.in_transaction,
                     transaction=logical(self), backups=[identity(p) for p in data.glob('*.before-v5.*.sqlite')])
                raise RuntimeError('UC-03-S8: failure after real jobs DDL')
            return cursor

        def rollback(self):
            was_transaction = self.in_transaction
            super().rollback()
            emit('rolled-back', was_transaction=was_transaction, in_transaction=self.in_transaction,
                 transaction=logical(self))

    if window != 'future':
        def observed_connect(*args, **kwargs):
            kwargs['factory'] = Boundary
            return connect(*args, **kwargs)
        sqlite3.connect = observed_connect
    import uvicorn
    from web.app import app
    from web.config import settings
    settings.DATA_DIR = str(data)
    # This is Uvicorn's real application startup and native failure exit code.
    uvicorn.run(app, host='127.0.0.1', port=0, lifespan='on', log_level='info')


def pid_alive(pid: int) -> bool:
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            state = kernel.WaitForSingleObject(handle, 0)
            assert state in (0, 258), state
            return state == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def failed_start(directory: Path, data: Path, window: str) -> dict:
    command = [sys.executable, '-c', "import runpy,sys; runpy.run_path(sys.argv[1])['startup_child']()",
               str(Path(__file__).resolve()), str(directory), str(data), window]
    record = {'command': command, 'cwd': str(directory), 'timeout_seconds': 30, 'started_at': time.time(),
              'log': str(directory / 'startup.log')}
    with (directory / 'startup.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=directory, env=subprocess_environment(), stdout=log,
                                   stderr=subprocess.STDOUT,
                                   start_new_session=os.name != 'nt',
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        record['launcher_pid'] = process.pid
        save(directory / 'process.json', record)
        try:
            record['exit_code'] = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            record['timed_out'] = True
            if os.name == 'nt':
                cleanup = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                         capture_output=True, text=True, timeout=10)
                record['cleanup'] = {'exit_code': cleanup.returncode, 'output': cleanup.stdout + cleanup.stderr}
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            record['exit_code'] = process.wait(timeout=10)
        finally:
            record['finished_at'] = time.time()
            if (directory / 'identity.json').exists():
                record['identity'] = json.loads((directory / 'identity.json').read_text(encoding='utf-8'))
                record['actual_pid_alive_after_exit'] = pid_alive(record['identity']['pid'])
            record['launcher_alive_after_exit'] = process.poll() is None
            save(directory / 'process.json', record)
    return record


def check_window(reference: Path, directory: Path, window: str) -> None:
    directory.mkdir(parents=True)
    data = directory / 'startup-copy'
    shutil.copytree(reference, data)
    database = data / 'catalog.sqlite'
    if window == 'future':
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('PRAGMA user_version=999')
            connection.commit()
    before = inspect_database(database)
    hashes = materials(data)
    evidence = {'window': window, 'before': before, 'materials_before': hashes,
                'backups_before': [str(p) for p in data.glob('*.before-v5.*.sqlite')]}
    save(directory / 'evidence.json', evidence)
    evidence['process'] = failed_start(directory, data, window)
    evidence['after'] = inspect_database(database)
    evidence['materials_after'] = materials(data)
    for name in ('injected', 'rolled-back'):
        path = directory / (name + '.json')
        evidence[name] = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    backups = sorted(data.glob('*.before-v5.*.sqlite'))
    evidence['backups'] = []
    for backup in backups:
        backup_state = inspect_database(backup)
        restored = directory / 'independent-recovery' / backup.stem
        restored.mkdir(parents=True)
        shutil.copy2(backup, restored / 'catalog.sqlite')
        for name in hashes:
            shutil.copytree(data / name, restored / name)
        recovery = inspect_database(restored / 'catalog.sqlite')
        evidence['backups'].append({'source_database': before['identity'], 'backup': backup_state,
                                    'recovery': recovery, 'recovery_materials': materials(restored)})
        assert identity(backup) == backup_state['identity'], '恢复核验改写了备份'
    save(directory / 'evidence.json', evidence)
    process = evidence['process']
    assert not process.get('timed_out'), '启动失败没有有界退出'
    assert process['exit_code'] == 3, '必须是 Uvicorn 的真实启动失败退出'
    assert process.get('identity') and not process['actual_pid_alive_after_exit']
    assert not process['launcher_alive_after_exit']
    log = (directory / 'startup.log').read_text(encoding='utf-8')
    assert 'Application startup failed. Exiting.' in log
    assert 'Application startup complete.' not in log and 'Uvicorn running on' not in log
    assert evidence['after']['logical'] == before['logical'], '旧版本、完整结构或记录未回滚'
    assert evidence['materials_after'] == hashes, '原件、修订或历史产物被改写'
    if window == 'future':
        assert '不支持的数据库版本：999' in log
        assert not evidence['backups'] and not evidence['injected']
        assert before['logical']['version'] == 999
    else:
        marker, rollback = evidence['injected'], evidence['rolled-back']
        assert marker and marker['in_transaction'] and len(marker['backups']) == 1
        assert marker['backups'][0] == evidence['backups'][0]['backup']['identity']
        assert rollback and rollback['was_transaction'] and not rollback['in_transaction']
        assert rollback['transaction'] == before['logical']
        changed = marker['transaction']['tables']
        assert 'owner_token' in [row[1] for row in changed['jobs']['columns']]
        assert marker['transaction'] != before['logical'], '没有真正执行迁移 DDL'
        if window in ('ddl', 'v4-ddl'):
            assert marker['boundary'] == 'after-real-jobs-rebuild'
            assert 'UC-03-S8: failure after real jobs DDL' in log
            assert marker['transaction']['version'] == (4 if window == 'v4-ddl' else 0)
        else:
            assert marker['boundary'] == 'final-foreign-key-check' and marker['version'] == 5
            assert not marker['valid_before'] and marker['violations']
            assert any(row[0] == 'exports' for row in marker['violations'])
            assert next(row for row in changed['exports']['columns'] if row[1] == 'revision_id')[3] == 0
            assert {row[0] for row in changed['schema_migrations']['records']} == {1, 2, 3, 4, 5}
            assert '数据库升级后的外键检查失败' in log
        assert len(evidence['backups']) == 1, '本次升级备份不唯一'
        for item in evidence['backups']:
            assert item['backup']['logical'] == before['logical']
            assert item['recovery']['logical'] == before['logical']
            assert item['backup']['logical']['integrity'] == [['ok']]
            assert item['backup']['identity']['sha256'] == item['recovery']['identity']['sha256']
            assert item['recovery_materials'] == hashes


def run(workdir: Path) -> dict:
    reference = workdir / 'independent-reference'
    provenance = build_reference(reference)
    save(workdir / 'reference.json', provenance)
    reference_hashes = tree_hashes(reference)
    failures = {}
    reference_v4 = workdir / 'independent-v4-reference'
    shutil.copytree(reference, reference_v4)
    with closing(sqlite3.connect(reference_v4 / 'catalog.sqlite')) as connection:
        connection.executescript((LEGACY_DDL.parent / 'catalog_v4_fixture.sql').read_text(encoding='utf-8'))
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 4
        assert 'selected_revision_id' not in [r[1] for r in connection.execute('PRAGMA table_info(jobs)')]
        assert not list(connection.execute('PRAGMA foreign_key_check'))
    save(workdir/'v4-reference.json',inspect_database(reference_v4/'catalog.sqlite'))
    windows = ('future', 'ddl', 'validation', 'v4-ddl', 'v4-validation')
    for window in windows:
        try:
            check_window(reference_v4 if window.startswith('v4-') else reference, workdir / window, window)
        except Exception:
            failures[window] = traceback.format_exc()
    save(workdir / 'example-results.json', {'windows': list(windows), 'failures': failures})
    # Successful REAL startup migration from v4; inspect all old rows and files,
    # the new FK and selection nullability, then reopen without another migration.
    from tests.common.delivery_support import web_client
    success = workdir/'v4-success'
    shutil.copytree(reference_v4,success)
    before = inspect_database(success/'catalog.sqlite')
    hashes = materials(success)
    with web_client(success) as client:
        assert client.get('/api/download/job-1').status_code == 200
    after = inspect_database(success/'catalog.sqlite')
    assert after['logical']['version'] == 5
    jobs = after['logical']['tables']['jobs']
    assert 'selected_revision_id' in [r[1] for r in jobs['columns']]
    assert any(r[2]=='revisions' and r[3]=='selected_revision_id' for r in jobs['foreign_keys'])
    assert [r[:-1] for r in jobs['records']] == before['logical']['tables']['jobs']['records']
    assert all(r[-1] is None for r in jobs['records'])
    for table,state in before['logical']['tables'].items():
        if table not in ('jobs','schema_migrations'):
            assert after['logical']['tables'][table] == state, table
    assert materials(success) == hashes
    from src.application.jobs import JobRuntime
    runtime = JobRuntime(success)
    selected = runtime.queue('job-1','html')
    assert selected.selected_revision_id == 'revision-1', '旧 v4 任务必须按自身 export 绑定'
    with closing(sqlite3.connect(success/'catalog.sqlite')) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            connection.execute("UPDATE jobs SET selected_revision_id='missing' WHERE job_id=?",(selected.job_id,))
        except sqlite3.IntegrityError:
            connection.rollback()
        else:
            raise AssertionError('新增修订绑定外键未执行')
    assert JobRuntime(success).get(selected.job_id).selected_revision_id == 'revision-1'
    save(workdir/'v4-success.json',{'before':before,'after':after,'materials':hashes,'selected_job':selected.__dict__})
    assert tree_hashes(reference) == reference_hashes, '独立参考来源被改写'
    assert not failures, failures
    return {'checks': ['未来版本经真实 Web 启动明确拒绝，版本、完整结构、全部记录与资料不变',
                       '独立历史旧结构经过真实备份与表重建后异常，实际事务完整回滚',
                       '完成真实迁移后在 SQLite 外键校验中发现注入的未提交坏关联，实际事务完整回滚',
                       '每次一致备份复制到独立恢复目录后只读打开，完整结构、全部记录、完整性与资料哈希匹配',
                       '五窗口真实 Uvicorn 启动失败退出 3，无就绪、无监听，launcher 与实际 Python 均退出',
                       '独立 schema-4 副本经真实 Web 启动迁移到 v5，保留全部旧行与资料，绑定外键执行且重开保持原任务准确修订'],
            'evidence': {'reference': str(workdir / 'reference.json'),
                         'results': str(workdir / 'example-results.json')},
            'limitations': ['合成异常副本不证明 S2 真实旧数据升级、正文迁移或实际用户库恢复',
                            '未复用 ownership_support.Child，T-04-7 仍待单独覆盖；强制超时清理不是本次通过范围']}


if __name__ == '__main__':
    from tests.common.support import run_one
    raise SystemExit(run_one(__file__))
