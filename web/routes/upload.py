"""Upload API endpoints."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, HttpUrl

from ..config import settings
from ..services.file_manager import FileManager, get_available_outputs, get_input_format
from ..services.task_manager import task_manager

router = APIRouter()


class UploadResponse(BaseModel):
    """Response for file upload."""

    task_id: str
    filename: str
    input_format: str
    available_outputs: list[str]


class FetchRequest(BaseModel):
    """Request for URL fetch."""

    url: HttpUrl


class FetchResponse(BaseModel):
    """Response for URL fetch."""

    task_id: str
    url: str
    input_format: str
    available_outputs: list[str]


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """Upload a file for conversion."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Check file size
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {settings.MAX_UPLOAD_SIZE // (1024*1024)}MB",
        )

    # Detect input format
    input_format = get_input_format(file.filename)
    if not input_format:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.filename}",
        )

    # Create task
    task = task_manager.create_task(file.filename, input_format)

    # Save file
    file_manager = FileManager(task.task_id)
    file_manager.save_upload(file.filename, content)

    return UploadResponse(
        task_id=task.task_id,
        filename=file.filename,
        input_format=input_format,
        available_outputs=get_available_outputs(input_format),
    )


@router.post("/fetch", response_model=FetchResponse)
async def fetch_url(request: FetchRequest):
    """Fetch URL for conversion."""
    url_str = str(request.url)

    # Create task
    task = task_manager.create_task(url_str, "url")

    # Save URL to file for later processing
    file_manager = FileManager(task.task_id)
    file_manager.save_upload("url.txt", url_str.encode("utf-8"))

    return FetchResponse(
        task_id=task.task_id,
        url=url_str,
        input_format="url",
        available_outputs=get_available_outputs("url"),
    )
