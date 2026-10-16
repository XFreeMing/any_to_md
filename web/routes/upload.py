"""Input transport; source bytes and job identity belong to the application."""

import asyncio

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, HttpUrl

from any_to_md.application.capabilities import CAPABILITIES, input_format
from any_to_md.application.jobs import ConversionFailure
from ..config import settings
from ..runtime import get_runtime

router = APIRouter()


class UploadResponse(BaseModel):
    task_id: str
    filename: str
    input_format: str
    available_outputs: list[str]


class FetchRequest(BaseModel):
    url: HttpUrl


class FetchResponse(BaseModel):
    task_id: str
    url: str
    input_format: str
    available_outputs: list[str]


@router.get('/capabilities')
def capabilities():
    return {name: {'suffixes': capability.suffixes, 'outputs': capability.outputs} for name, capability in CAPABILITIES.items()}


@router.post('/upload', response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail='No filename provided')
    format_name = input_format(file.filename)
    if format_name is None:
        raise HTTPException(status_code=400, detail=f'Unsupported file type: {file.filename}')
    content = await file.read(settings.MAX_UPLOAD_SIZE + 1)
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail='File exceeds the configured upload limit')
    job = await asyncio.to_thread(get_runtime().upload, file.filename, content)
    return UploadResponse(task_id=job.job_id, filename=job.filename, input_format=job.input_format, available_outputs=list(CAPABILITIES[job.input_format].outputs))


@router.post('/fetch', response_model=FetchResponse)
async def fetch_url(request: FetchRequest):
    url = str(request.url)
    try:
        job = await asyncio.to_thread(get_runtime().upload, 'webpage.url', url.encode('utf-8'), 'url')
    except ConversionFailure as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return FetchResponse(task_id=job.job_id, url=url, input_format='url', available_outputs=list(CAPABILITIES['url'].outputs))
