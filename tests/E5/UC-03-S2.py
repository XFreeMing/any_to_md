"""UC-03-S2：升级真实旧资料副本并继续下载（E5 / US-05）。

作为已有资料的使用者，我希望升级后保留历史版本，以便安全取得新旧结果。
Given 已停止写入、含原件、旧修订与历史导出的真实旧数据库一致副本。
When 在另一个隔离副本启动新版运行时，并从原件重新导出 Markdown。
Then 升级备份可恢复，旧文件及下载哈希不变，新旧修订关联可查询，来源副本不被改写。

此处不升级原库，也不覆盖缺原件或仅剩 Markdown 的迁移；未知版本 / 失败升级另见 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
import os
import shutil
import sqlite3
from pathlib import Path

from src.application.convert import ConversionRequest, convert
from src.application.jobs import JobRuntime
from tests.common.delivery_support import digest, tree_hashes, web_client
from tests.common.support import Blocked


def run(workdir: Path) -> dict:
    location = os.environ.get('ANY_TO_MD_LEGACY_DATA_DIR')
    if not location:
        raise Blocked('需要 ANY_TO_MD_LEGACY_DATA_DIR 指向已停用且含原件、旧修订、历史导出的真实旧数据副本；不以合成数据代替。')
    original = Path(location).expanduser().resolve()
    assert original.is_dir() and (original / 'catalog.sqlite').is_file(), '真实旧数据目录不存在'
    assert not workdir.resolve().is_relative_to(original), '场景工作目录不能位于旧数据来源内'
    assert not any(path.is_symlink() for path in original.rglob('*')), '旧数据副本应先解开并核实符号链接'
    if any((original / name).exists() for name in ('catalog.sqlite-wal', 'catalog.sqlite-shm')):
        raise Blocked('旧数据仍有 WAL/SHM，请先取得关闭数据库后的完整一致副本。')
    before_source = tree_hashes(original)
    data = workdir / 'copied-legacy-data'
    shutil.copytree(original, data)
    try:
        with sqlite3.connect(f'file:{data / "catalog.sqlite"}?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version >= 4:
                raise Blocked('所给数据已经是当前数据库结构，不能证明旧结构升级。')
            preserved = {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table}')]
                         for table in ('sources', 'revisions', 'exports', 'artifacts')}
            candidates = list(connection.execute('''SELECT r.revision_id,r.content_storage_key,s.storage_key,
                e.job_id FROM revisions r JOIN sources s ON s.edition_id=r.edition_id
                JOIN exports e ON e.revision_id=r.revision_id
                WHERE r.content_storage_key IS NOT NULL AND e.status IN ('completed','completed_with_warnings','partial')'''))
        candidate = next((dict(row) for row in candidates
                          if (data / row['storage_key']).is_file()
                          and (data / row['content_storage_key']).is_file()
                          and json.loads((data / row['content_storage_key']).read_text()).get('schema_version') == 1), None)
        if candidate is None:
            raise Blocked('旧数据缺少可从保留原件重建、且已有成功历史导出的 schema-1 修订。')
        before_files = {path: value for path, value in tree_hashes(data).items()
                        if path.startswith(('sources/', 'revisions/', 'exports/'))}
        backups_before = set(data.glob('catalog.before-v*.sqlite'))
        runtime = JobRuntime(data)
        backups = set(data.glob('catalog.before-v*.sqlite')) - backups_before
        assert len(backups) == 1, '升级未留下唯一的新备份'
        backup = next(iter(backups))
        with sqlite3.connect(f'file:{backup}?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            for table, rows in preserved.items():
                assert [dict(row) for row in connection.execute(f'SELECT * FROM {table}')] == rows, table
        old_job_id = candidate['job_id']
        old_download = runtime.download(old_job_id)
        old_download_hash = digest(old_download)
        with web_client(data) as client:
            state = client.get('/api/status/' + old_job_id).json()
            assert state['download_url'], state
            assert client.get(state['download_url']).status_code == 200
        new_job = runtime.queue(old_job_id, 'md_single')
        result = convert(ConversionRequest(data_dir=data, job_id=new_job.job_id))
        assert result.status in ('completed', 'completed_with_warnings'), result.status
        assert result.revision_id != candidate['revision_id'] and result.job_id != old_job_id
        assert runtime.download(result.job_id).is_file()
        assert digest(runtime.download(old_job_id)) == old_download_hash
        for relative, expected in before_files.items():
            assert (data / relative).is_file() and digest(data / relative) == expected, relative
        with sqlite3.connect(data / 'catalog.sqlite') as connection:
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            lineage = connection.execute('SELECT old_revision_id,new_revision_id FROM revision_migrations').fetchall()
            assert (candidate['revision_id'], result.revision_id) in lineage
        return {'checks': ['真实旧数据只在隔离副本升级，升级前备份完整可恢复',
                           '历史原件、修订与导出哈希不变，旧任务仍可下载',
                           '从保留原件发布新修订且旧→新关系可追溯']}
    finally:
        assert tree_hashes(original) == before_source, '真实旧数据来源被意外修改'


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
