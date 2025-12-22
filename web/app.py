"""FastAPI application for document conversion service."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routes import convert, download, upload
from .services.file_manager import cleanup_expired_files


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup: ensure directories exist
    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    settings.outputs_path.mkdir(parents=True, exist_ok=True)

    # Start background cleanup task
    cleanup_task = asyncio.create_task(periodic_cleanup())

    yield

    # Shutdown: cancel cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


async def periodic_cleanup():
    """Periodically clean up expired files."""
    while True:
        await asyncio.sleep(300)  # Run every 5 minutes
        cleanup_expired_files()


app = FastAPI(
    title="Any to Markdown",
    description="Document format conversion service",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Include API routes
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(convert.router, prefix="/api", tags=["convert"])
app.include_router(download.router, prefix="/api", tags=["download"])


@app.get("/")
async def root():
    """Serve the main page."""
    index_path = static_path / "index.html"
    if index_path.exists():
        from fastapi.responses import FileResponse

        return FileResponse(index_path)
    return {"message": "Any to Markdown API", "docs": "/docs"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


def main():
    """Run the application."""
    uvicorn.run(
        "web.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )


if __name__ == "__main__":
    main()
