"""Configuration settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # OCR API Configuration
    OCR_API_URL: str = "http://localhost:9122/api/v1/ocr/process"
    OCR_TIMEOUT: int = 600  # 10 minutes for large files
    OCR_RETRY_COUNT: int = 2

    # File Storage
    DATA_DIR: str = "var"
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8888

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }



settings = Settings()
