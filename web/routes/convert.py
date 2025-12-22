"""Conversion API endpoints."""

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..services.converter import ConversionService
from ..services.file_manager import FileManager, get_available_outputs
from ..services.task_manager import Task, TaskStatus, task_manager

router = APIRouter()


class ConvertRequest(BaseModel):
    """Request for conversion."""

    task_id: str
    output_format: str


class ConvertResponse(BaseModel):
    """Response for conversion request."""

    task_id: str
    status: str


class StatusResponse(BaseModel):
    """Response for status query."""

    task_id: str
    status: str
    progress: int
    message: str
    download_url: str | None = None


@router.post("/convert", response_model=ConvertResponse)
async def start_conversion(
    request: ConvertRequest,
    background_tasks: BackgroundTasks,
):
    """Start a conversion job."""
    task = task_manager.get_task(request.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status == TaskStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="Conversion already in progress")

    # Validate output format
    available = get_available_outputs(task.input_format)
    if request.output_format not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid output format. Available: {available}",
        )

    # Update task with output format
    task.output_format = request.output_format
    task_manager.update_task(
        request.task_id,
        status=TaskStatus.PENDING,
        message="Queued for conversion",
    )

    # Start conversion in background
    background_tasks.add_task(run_conversion, task)

    return ConvertResponse(
        task_id=task.task_id,
        status=task.status.value,
    )


async def run_conversion(task: Task):
    """Run conversion in background."""
    file_manager = FileManager(task.task_id)
    service = ConversionService(task, file_manager)

    try:
        await service.convert(task.output_format)
    except Exception as e:
        task_manager.update_task(
            task.task_id,
            status=TaskStatus.FAILED,
            message=str(e),
        )


@router.get("/status/{task_id}", response_model=StatusResponse)
async def get_status(task_id: str):
    """Get conversion status."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    download_url = None
    if task.status == TaskStatus.COMPLETED and task.download_filename:
        download_url = f"/api/download/{task_id}"

    return StatusResponse(
        task_id=task.task_id,
        status=task.status.value,
        progress=task.progress,
        message=task.message,
        download_url=download_url,
    )
