"""Bounded HTTP capture and immutable replay; no output conversion lives here."""

from __future__ import annotations

import io
import json
import mimetypes
from email.message import Message
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit
from zipfile import ZipFile

import httpx
from bs4 import BeautifulSoup
from bs4.element import Stylesheet

from .archive import validate_archive
from .http import read_response_body
from .html_source import _html_references, MAX_SOURCE_BYTES, MAX_SOURCE_FILES
from ..content.styles import style_references, rewrite_stylesheet_resources

MANIFEST = 'url-source.json'
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('网页来源只接受无凭据的 HTTP/HTTPS URL')
    return urldefrag(value)[0]


def article_document(content: bytes | str, final_url: str, content_type: str = '') -> tuple[BeautifulSoup, bool]:
    """Keep source DOM structure inside the declared main/article region.

    Unmarked pages use the cleaned body and explicitly report that boundary.
    No statistical extractor or Markdown round trip can drop table/code nodes.
    """
    header = Message()
    header['content-type'] = content_type
    soup = BeautifulSoup(content, 'html.parser', from_encoding=header.get_content_charset() if isinstance(content, bytes) else None)
    for node in soup.select('nav, footer, script, iframe, object, embed, [role=navigation], [role=banner], [role=complementary]'):
        node.decompose()
    for node in soup.find_all('aside'):
        if node.parent is None:
            continue
        if node.get('role') not in {'doc-footnote', 'doc-endnote'} and not any('footnote' in value for value in node.get('class', [])):
            node.decompose()
    for node in soup.select('a.headerlink[href^="#"]'):
        if node.find_parent(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']) and node.get_text(strip=True) in {'¶', '#', '🔗'}:
            node.decompose()
    region = soup.find('main') or soup.find(attrs={'role': 'main'}) or soup.find('article')
    marked = region is not None
    document = BeautifulSoup('<html><head></head><body></body></html>', 'html.parser')
    if soup.html:
        document.html.attrs.update(soup.html.attrs)
    if soup.body:
        document.body.attrs.update(soup.body.attrs)
    for node in (document.html, document.body):
        node['data-url-context'] = ''
    if soup.head:
        for node in list(soup.head.contents):
            document.head.append(node.extract())
    else:
        for node in soup.find_all(['title', 'base', 'meta', 'link']):
            document.head.append(node.extract())
    for node in soup.find_all('style'):
        document.head.append(node.extract())
    if not document.title:
        title = document.new_tag('title')
        heading = (region or soup).find('h1')
        title.string = heading.get_text(' ', strip=True) if heading else urlsplit(final_url).hostname
        document.head.append(title)
    if region is not None:
        region['data-url-context'] = ''
        parents = []
        for parent in region.parents:
            if parent.name in {'body', 'html', '[document]'}:
                break
            parents.append((parent.name, dict(parent.attrs)))
        selected = region.extract()
        for name, attrs in parents:
            wrapper = document.new_tag(name, attrs=attrs)
            wrapper['data-url-context'] = ''
            wrapper.append(selected)
            selected = wrapper
        document.body.append(selected)
    else:
        for node in list((soup.body or soup).contents):
            document.body.append(node.extract())
    # The original <base>, if present, is interpreted against the redirect URL.
    base = document.find('base', href=True)
    if base:
        base['href'] = urljoin(final_url, str(base['href']))
    # Site chrome can leave floated/flex ancestors around the retained article.
    # Keep their selector/base context, but put the reading shell in block flow
    # so it can paginate. Inner tables, code, figures and author styles survive.
    style = document.new_tag('style')
    style.append(Stylesheet('[data-url-context] {display:block!important;float:none!important;position:static!important;width:auto!important;min-width:0!important;max-width:none!important;margin:0!important;}'))
    document.head.append(style)
    if not document.body.get_text(strip=True) and not document.body.find(['img', 'svg', 'table', 'pre']):
        raise ValueError('网页没有可提取的正文')
    return document, marked


def _dependencies(content: bytes | str, base: str, media_type: str) -> list[str]:
    if media_type == 'text/css':
        text = content.decode('utf-8', errors='replace') if isinstance(content, bytes) else content
        references = [(value, base) for value in style_references(text)]
    else:
        soup = BeautifulSoup(content, 'html.parser')
        # Links are destinations, not resources or pages to crawl.
        for link in soup.find_all('a'):
            link.attrs.pop('href', None)
        for link in soup.find_all('link'):
            relations = {str(value).lower() for value in link.get('rel', [])}
            if 'stylesheet' not in relations or 'alternate' in relations or link.has_attr('disabled'):
                link.decompose()
        references = _html_references(str(soup).encode(), base)
    return list(dict.fromkeys(urldefrag(urljoin(origin, value))[0] for value, origin in references
                              if value and not value.startswith('#') and urlsplit(urljoin(origin, value)).scheme in {'http', 'https'}))


def _response(client: httpx.Client, url: str, remaining: int) -> tuple[bytes, str, str, dict]:
    with client.stream('GET', http_url(url), headers={'Accept-Encoding': 'identity'}) as response:
        response.raise_for_status()
        http_url(str(response.url))
        data = read_response_body(response, min(MAX_RESPONSE_BYTES, remaining))
        media = response.headers.get('content-type', '').split(';', 1)[0].lower()
        return bytes(data), str(response.url), media, dict(response.headers)


def capture_url_source(url: str, *, client: httpx.Client | None = None) -> bytes:
    if client is None:
        with httpx.Client(timeout=30, follow_redirects=True, max_redirects=10) as session:
            return capture_url_source(url, client=session)
    url = http_url(url)
    raw, final, media, headers = _response(client, url, MAX_SOURCE_BYTES)
    if media and media not in {'text/html', 'application/xhtml+xml'}:
        raise ValueError(f'网页响应不是 HTML：{media}')
    document, _ = article_document(raw, final, headers.get('content-type', ''))
    members = {'response.html': raw}
    manifest = {'format': 'url-source-v1', 'url': url, 'final_url': final, 'headers': headers, 'resources': {}, 'failures': []}
    pending = [(value, 0) for value in _dependencies(str(document), final, 'text/html')]
    seen = set()
    total = len(raw)
    while pending:
        target, depth = pending.pop(0)
        if target in seen:
            continue
        seen.add(target)
        if len(seen) >= MAX_SOURCE_FILES or depth > 32:
            raise ValueError('网页资源超过数量或 32 层引用深度限制')
        try:
            data, resource_url, content_type, response_headers = _response(client, target, MAX_SOURCE_BYTES-total)
        except httpx.HTTPError as error:
            manifest['failures'].append({'url': target, 'error': str(error)})
            continue
        total += len(data)
        name = f'resources/{len(manifest["resources"]):06d}'
        members[name] = data
        content_type = content_type or mimetypes.guess_type(urlsplit(resource_url).path)[0] or 'application/octet-stream'
        manifest['resources'][target] = {'member': name, 'url': resource_url, 'media_type': content_type, 'headers': response_headers}
        if content_type in {'text/css', 'image/svg+xml'}:
            pending.extend((value, depth+1) for value in _dependencies(data, resource_url, content_type))
    members[MANIFEST] = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    if sum(map(len, members.values())) > MAX_SOURCE_BYTES:
        raise ValueError('网页快照累计大小超过限制')
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as archive:
        for name, data in sorted(members.items()):
            archive.writestr(name, data)
    return stream.getvalue()


@dataclass(frozen=True)
class UrlSource:
    manifest: dict
    members: dict[str, bytes]

    def resource(self, name: str) -> tuple[bytes, str | None] | None:
        record = self.manifest['resources'].get(name)
        if record is None:
            return None
        data, media = self.members[record['member']], record['media_type']
        # Relative dependencies use the final response URL, including redirects.
        if media == 'text/css':
            data = rewrite_stylesheet_resources(data.decode('utf-8', errors='replace'), lambda value: value if value.startswith(('#', 'data:')) else urljoin(record['url'], value)).encode()
        elif media == 'image/svg+xml':
            from xml.etree import ElementTree as ET
            try:
                root = ET.fromstring(data)
                key = '{http://www.w3.org/XML/1998/namespace}base'
                root.set(key, urljoin(record['url'], root.get(key, '')))
                data = ET.tostring(root)
            except ET.ParseError:
                pass  # The shared media normalizer reports malformed SVG.
        return data, media


def read_url_source(path: Path) -> UrlSource:
    with ZipFile(path) as archive:
        members = {name: archive.read(item) for name, item in validate_archive(archive, max_files=MAX_SOURCE_FILES, max_bytes=MAX_SOURCE_BYTES)}
    manifest = json.loads(members[MANIFEST])
    if manifest.get('format') != 'url-source-v1':
        raise ValueError('未知网页快照版本')
    http_url(manifest['url'])
    http_url(manifest['final_url'])
    for record in manifest['resources'].values():
        if record['member'] not in members:
            raise ValueError('网页快照缺少资源')
    return UrlSource(manifest, members)
