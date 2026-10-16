"""Download only cataloged artifacts from completed publications."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from any_to_md.export.naming import title_slug
from ..runtime import get_runtime

router = APIRouter()


@router.get('/download/{task_id}')
def download_file(task_id: str):
    runtime = get_runtime()
    job = runtime.get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail='Task not found')
    try:
        path = runtime.download(task_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(path=path, filename=title_slug(Path(job.filename).stem) + path.suffix)
