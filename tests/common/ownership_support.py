"""Process and HTTP evidence transport for the S7 ownership windows.

Hooks pause outside transactions and call the original ownership operations.
The scenario owns the ordering, business conditions and assertions.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

import httpx

from tests.common.delivery_support import subprocess_environment, tree_hashes


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def job_row(data: Path, job_id: str) -> dict:
    with closing(sqlite3.connect(data / 'catalog.sqlite')) as connection:
        connection.row_factory = sqlite3.Row
        return dict(connection.execute('''SELECT j.*, e.export_id, e.storage_key FROM jobs j
            LEFT JOIN exports e ON e.job_id=j.job_id WHERE j.job_id=?''', (job_id,)).fetchone())


def snapshot(data: Path) -> dict:
    with closing(sqlite3.connect(data / 'catalog.sqlite')) as connection:
        connection.row_factory = sqlite3.Row
        database = {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    for table in ('jobs', 'exports', 'artifacts', 'sources', 'revisions')}
    return {'time': time.time(), 'database': database,
            'hashes': {name: tree_hashes(data / name)
                       for name in ('sources', 'revisions', 'exports', 'staging', 'jobs')}}


def wait_until(predicate, message: str, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result:
            return result
        assert time.monotonic() < deadline, message
        time.sleep(0.05)


class Child:
    def __init__(self, directory: Path, data: Path, role: str, window: str, job_id: str | None = None):
        self.directory = directory
        directory.mkdir(parents=True)
        self.log = (directory / 'process.log').open('w', encoding='utf-8')
        self.command = [sys.executable, '-c',
                        'from tests.common.ownership_support import child; child()',
                        str(directory), str(data), role, window, job_id or '']
        self.process = subprocess.Popen(self.command, cwd=directory, env=subprocess_environment(),
                                        stdout=self.log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        self.record = {'launcher_pid': self.process.pid, 'command': self.command, 'cwd': str(directory),
                       'role': role, 'window': window, 'log': str(directory / 'process.log')}
        self.pid = self.event('identity')['pid']
        self.record['pid'] = self.pid
        save(directory / 'process.json', self.record)
        self.base_url = None

    def events(self, name: str) -> list[dict]:
        return [json.loads(path.read_text(encoding='utf-8'))
                for path in sorted((self.directory / 'events').glob('*-' + name + '.json'))]

    def event(self, name: str, timeout: float = 30) -> dict:
        def observe():
            events = self.events(name)
            if events:
                return events[-1]
            assert self.process.poll() is None, f'{name}: child exited; see {self.directory / "process.log"}'
        return wait_until(observe, f'{name}: boundary not reached', timeout)

    def release(self, name: str) -> None:
        save(self.directory / (name + '.release'), {'time': time.time()})

    def ready(self) -> None:
        self.base_url = self.event('listening')['base_url']

        def health():
            assert self.process.poll() is None, f'service exited: {self.directory}'
            try:
                return self.request('GET', '/health').status_code == 200
            except httpx.HTTPError:
                return False
        wait_until(health, f'service not ready: {self.directory}')

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        record = {'time': time.time(), 'method': method, 'url': self.base_url + path,
                  'json': kwargs.get('json')}
        if 'files' in kwargs:
            record['files'] = {key: {'name': value[0], 'sha256': hashlib.sha256(value[1]).hexdigest(),
                                     'size_bytes': len(value[1])} for key, value in kwargs['files'].items()}
        target = self.directory / 'http' / (str(time.time_ns()) + '.json')
        try:
            response = httpx.request(method, record['url'], timeout=2, **kwargs)
            record.update(response_time=time.time(), status=response.status_code, headers=dict(response.headers),
                          response_sha256=hashlib.sha256(response.content).hexdigest(), size_bytes=len(response.content))
            if 'json' in response.headers.get('content-type', ''):
                record['response'] = response.json()
            else:
                payload = target.with_suffix('.body')
                payload.parent.mkdir(parents=True, exist_ok=True)
                payload.write_bytes(response.content)
                record['response_file'] = str(payload)
            return response
        except httpx.HTTPError as error:
            record['error'] = str(error)
            raise
        finally:
            save(target, record)

    def finish(self, timeout: float = 30) -> int:
        try:
            return self.process.wait(timeout=timeout)
        finally:
            self.record['exit_code'] = self.process.poll()
            save(self.directory / 'process.json', self.record)

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                # Release only this child's paused work before graceful shutdown.
                # A Windows venv launcher can have a different PID from Python.
                self.release('convert')
                self.release('recover')
                self.release('stop')
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.record['forced_cleanup'] = True
                    self.process.terminate()
                    self.process.wait(timeout=10)
            self.record.update(exit_code=self.process.returncode, stopped_at=time.time())
            save(self.directory / 'process.json', self.record)
        finally:
            self.log.close()


def child() -> None:
    directory, data = map(Path, sys.argv[1:3])
    role, window, job_id = sys.argv[3:6]
    from src.adapters.persistence.sqlite import SQLiteCatalog
    from src.application import jobs
    from src.application.convert import ConversionRequest, convert
    from src.domain.jobs import JobConflict

    def emit(name: str, **value):
        event = {'name': name, 'pid': os.getpid(), 'time': time.time(), **value}
        save(directory / 'events' / (str(time.time_ns()) + '-' + name + '.json'), event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def gate(name: str):
        wait_until(lambda: (directory / (name + '.release')).exists(), f'{name}: release timed out', 45)
        emit(name + '-released')

    import platform
    emit('identity', executable=sys.executable, python=sys.version, platform=platform.platform(),
         role=role, window=window, cwd=str(Path.cwd()), parent_pid=os.getppid())

    # Only shorten the scenario process's lease. The real heartbeat and SQLite
    # ownership / CAS / publish transactions are kept intact.
    jobs.LEASE_SECONDS = 6
    claim = SQLiteCatalog.claim_job

    def claim_observed(self, task, token, pid, lease):
        emit('claim-attempt', job_id=task, token=token, owner_pid=pid, lease=lease)
        try:
            result = claim(self, task, token, pid, lease)
        except JobConflict as error:
            emit('claim-conflict', job_id=task, token=token, error=str(error), row=job_row(data, task))
            raise
        emit('claimed', job_id=task, token=token, row=job_row(data, task))
        return result
    SQLiteCatalog.claim_job = claim_observed

    if role == 'competitor':
        try:
            result = convert(ConversionRequest(data_dir=data, job_id=job_id))
        except JobConflict as error:
            emit('conversion-conflict', job_id=job_id, error=str(error))
        else:
            emit('unexpected-conversion', job_id=result.job_id, export_id=result.export_id)
        return

    if role == 'owner':
        enter = jobs.JobExecution.__enter__
        begin = jobs.JobExecution.begin
        renew = SQLiteCatalog.renew_job

        def enter_paused(self):
            if window == 'queued':
                emit('paused', job_id=self.job_id, row=job_row(data, self.job_id), boundary='before-claim')
                gate('convert')
            return enter(self)

        def begin_paused(self, *args, **kwargs):
            root = begin(self, *args, **kwargs)
            if window != 'queued':
                emit('paused', job_id=self.job_id, token=self.token, export_id=self.export_id,
                     row=job_row(data, self.job_id), boundary='after-start-export')
                gate('convert')
            return root

        def renew_observed(self, task, token, lease):
            before = job_row(data, task)
            started = time.time()
            result = renew(self, task, token, lease)
            emit('renewed', job_id=task, token=token, started_at=started,
                 before=before, requested_lease=lease, after=job_row(data, task))
            return result

        jobs.JobExecution.__enter__ = enter_paused
        jobs.JobExecution.begin = begin_paused
        SQLiteCatalog.renew_job = renew_observed

    active = SQLiteCatalog.active_jobs
    interrupt = SQLiteCatalog.interrupt_job
    recover = jobs.JobRuntime.recover

    def active_observed(self):
        rows = active(self)
        emit('recovery-snapshot', rows=rows)
        if role == 'recovery' and window == 'renewal' and rows:
            gate('recover')
        return rows

    def interrupt_observed(self, task, pid, lease, report_key):
        before = job_row(data, task)
        started = time.time()
        result = interrupt(self, task, pid, lease, report_key)
        emit('cas-result', job_id=task, expected_pid=pid, expected_lease=lease, report_key=report_key,
             started_at=started, result=result, before=before, after=job_row(data, task))
        return result

    def recover_observed(self):
        result = recover(self)
        emit('recovered', count=result)
        return result

    SQLiteCatalog.active_jobs = active_observed
    SQLiteCatalog.interrupt_job = interrupt_observed
    jobs.JobRuntime.recover = recover_observed

    import uvicorn
    from web.app import app
    from web.config import settings
    settings.DATA_DIR = str(data)
    # Child binds its own socket, avoiding Windows pass_fds and port races.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        emit('listening', base_url=f'http://127.0.0.1:{listener.getsockname()[1]}')
        server = uvicorn.Server(uvicorn.Config(app, log_level='info'))

        def stop():
            while not (directory / 'stop.release').exists():
                time.sleep(0.05)
            server.should_exit = True
        threading.Thread(target=stop, daemon=True).start()
        try:
            server.run(sockets=[listener])
        finally:
            emit('server-exited')
