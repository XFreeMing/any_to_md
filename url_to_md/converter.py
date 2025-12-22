"""URL content extraction and conversion to Markdown."""

import re
from typing import Optional
from urllib.parse import urlparse

import httpx
import trafilatura


async def fetch_url_to_markdown(
    url: str,
    timeout: int = 30,
) -> tuple[str, Optional[str]]:
    """
    Fetch webpage content and convert to Markdown.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Tuple of (markdown_content, title or None)

    Raises:
        Exception: If fetching or conversion fails
    """
    # Fetch the page
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        html_content = response.text

    # Extract main content using trafilatura
    extracted = trafilatura.extract(
        html_content,
        include_comments=False,
        include_tables=True,
        include_images=True,
        include_links=True,
        output_format="markdown",
    )

    if not extracted:
        raise Exception("Could not extract content from the webpage")

    # Try to get the title
    title = _extract_title(html_content, url)

    # Add metadata header
    md_content = f"# {title}\n\n" if title else ""
    md_content += f"> Source: {url}\n\n"
    md_content += extracted

    return md_content, title


def _extract_title(html_content: str, url: str) -> Optional[str]:
    """Extract page title from HTML or URL."""
    # Try to find <title> tag
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html_content, re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()
        # Clean up common suffixes
        for suffix in [" - ", " | ", " :: ", " — "]:
            if suffix in title:
                title = title.split(suffix)[0].strip()
        return title

    # Fallback to domain name
    parsed = urlparse(url)
    return parsed.netloc


def fetch_url_to_markdown_sync(
    url: str,
    timeout: int = 30,
) -> tuple[str, Optional[str]]:
    """
    Synchronous version of fetch_url_to_markdown.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Tuple of (markdown_content, title or None)
    """
    # Fetch the page
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        html_content = response.text

    # Extract main content using trafilatura
    extracted = trafilatura.extract(
        html_content,
        include_comments=False,
        include_tables=True,
        include_images=True,
        include_links=True,
        output_format="markdown",
    )

    if not extracted:
        raise Exception("Could not extract content from the webpage")

    # Try to get the title
    title = _extract_title(html_content, url)

    # Add metadata header
    md_content = f"# {title}\n\n" if title else ""
    md_content += f"> Source: {url}\n\n"
    md_content += extracted

    return md_content, title
