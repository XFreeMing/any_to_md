"""Conversion endpoints backed by the common durable job repository."""

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from src.application.jobs import ConversionFailure, JobRuntime
from src.domain.jobs import Job, JobConflict
from ..runtime import get_runtime
from ..services.converter import ConversionService

router = APIRouter()
logger = logging.getLogger(__name__)


class ConvertRequest(BaseModel):
    task_id: str
    output_format: str


class ConvertResponse(BaseModel):
    task_id: str
    status: str


class StatusResponse(BaseModel):
    task_id: str
    status: str
    progress: int
    message: str
    download_url: str | None = None


@router.post('/convert', response_model=ConvertResponse)
async def start_conversion(request: ConvertRequest, background_tasks: BackgroundTasks):
    runtime = get_runtime()
    try:
        job = await asyncio.to_thread(runtime.queue, request.task_id, request.output_format)
    except KeyError:
        raise HTTPException(status_code=404, detail='Task not found') from None
    except JobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    background_tasks.add_task(run_conversion, job, runtime)
    return ConvertResponse(task_id=job.job_id, status=job.status.value)


async def run_conversion(job: Job, runtime: JobRuntime):
    try:
        await ConversionService(job, runtime).convert(job.output_format)
    except (ConversionFailure, JobConflict) as error:
        # The runtime already recorded the terminal outcome. The HTTP layer
        # never writes a second, competing task status.
        logger.error('Conversion stopped: %s', error)


@router.get('/status/{task_id}', response_model=StatusResponse)
def get_status(task_id: str):
    job = get_runtime().get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail='Task not found')
    return StatusResponse(task_id=job.job_id, status=job.status.value, progress=job.progress, message=job.message, download_url=f'/api/download/{job.job_id}' if job.status.downloadable and job.download_relative else None)
