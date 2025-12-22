"""Download API endpoints."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services.file_manager import FileManager
from ..services.task_manager import TaskStatus, task_manager

router = APIRouter()


@router.get("/download/{task_id}")
async def download_file(task_id: str):
    """Download converted file."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Conversion not completed")

    if not task.download_filename:
        raise HTTPException(status_code=404, detail="No output file available")

    file_manager = FileManager(task_id)
    file_path = file_manager.get_output_path(task.download_filename)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    # Determine media type
    suffix = file_path.suffix.lower()
    media_types = {
        ".md": "text/markdown",
        ".html": "text/html",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        path=file_path,
        filename=task.download_filename,
        media_type=media_type,
    )
