"""OCR conversion using DeepSeek OCR API."""

import zipfile
from pathlib import Path
from typing import Optional

import httpx

# Import settings lazily to avoid circular imports
def _get_settings():
    from web.config import settings
    return settings


async def convert_with_ocr(
    file_path: Path | str,
    output_dir: Path | str,
    api_url: Optional[str] = None,
    timeout: Optional[int] = None,
) -> tuple[Path, Optional[Path]]:
    """
    Convert PDF or image to Markdown using DeepSeek OCR API.

    Args:
        file_path: Path to the input file (PDF or image)
        output_dir: Directory for output files
        api_url: OCR API URL (defaults to settings.OCR_API_URL)
        timeout: Request timeout in seconds (defaults to settings.OCR_TIMEOUT)

    Returns:
        Tuple of (markdown_path, images_dir or None if no images)

    Raises:
        Exception: If OCR API call fails
    """
    file_path = Path(file_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get settings
    settings = _get_settings()
    api_url = api_url or settings.OCR_API_URL
    timeout = timeout or settings.OCR_TIMEOUT

    # Call OCR API
    async with httpx.AsyncClient(timeout=timeout) as client:
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f)}
            response = await client.post(api_url, files=files)

        if response.status_code != 200:
            error_detail = response.text
            try:
                error_json = response.json()
                error_detail = error_json.get("detail", error_detail)
            except Exception:
                pass
            raise Exception(f"OCR API error ({response.status_code}): {error_detail}")

        # Save ZIP response
        zip_path = output_dir / "ocr_result.zip"
        zip_path.write_bytes(response.content)

        # Extract ZIP
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(output_dir)

        # Clean up ZIP
        zip_path.unlink()

        # Find output files
        md_path = output_dir / "result.md"
        images_dir = output_dir / "images"

        if not md_path.exists():
            # Check if there's a different markdown file
            md_files = list(output_dir.glob("*.md"))
            if md_files:
                md_path = md_files[0]
            else:
                raise Exception("OCR API did not return a Markdown file")

        return md_path, images_dir if images_dir.exists() else None


async def check_ocr_service(api_url: Optional[str] = None) -> dict:
    """
    Check if OCR service is available.

    Args:
        api_url: OCR API URL (defaults to settings.OCR_API_URL)

    Returns:
        Dict with status information
    """
    settings = _get_settings()
    api_url = api_url or settings.OCR_API_URL

    # Extract base URL (remove /api/v1/ocr/process path)
    base_url = api_url.rsplit("/api/", 1)[0]
    health_url = f"{base_url}/health"

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(health_url)

            if response.status_code == 200:
                return {"available": True, "status": "healthy"}
            else:
                return {"available": False, "status": f"unhealthy ({response.status_code})"}

    except httpx.ConnectError:
        return {"available": False, "status": "connection refused"}
    except httpx.TimeoutException:
        return {"available": False, "status": "timeout"}
    except Exception as e:
        return {"available": False, "status": str(e)}
