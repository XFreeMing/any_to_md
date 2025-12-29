"""Configuration settings loaded from environment variables."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # OCR API Configuration
    OCR_API_URL: str = "http://localhost:9122/api/v1/ocr/process"
    OCR_TIMEOUT: int = 600  # 10 minutes for large files
    OCR_RETRY_COUNT: int = 2

    # PDF Detection Threshold
    PDF_TEXT_THRESHOLD: int = 50  # Characters per page below this = scanned PDF

    # File Storage
    TEMP_DIR: str = "temp"
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB
    FILE_EXPIRE_SECONDS: int = 3600  # 1 hour

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8888

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @property
    def temp_path(self) -> Path:
        """Get temp directory as Path object."""
        return Path(self.TEMP_DIR)

    @property
    def uploads_path(self) -> Path:
        """Get uploads directory path."""
        return self.temp_path / "uploads"

    @property
    def outputs_path(self) -> Path:
        """Get outputs directory path."""
        return self.temp_path / "outputs"


settings = Settings()
