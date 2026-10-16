"""Bounded HTTP response decoding shared by URL acquisition and OCR."""

import zlib

import httpx


def read_response_body(response: httpx.Response, limit: int) -> bytes:
    data = bytearray()
    encoding = response.headers.get('content-encoding', 'identity').lower().strip()
    if encoding not in {'identity', 'gzip'}:
        raise ValueError(f'HTTP响应使用不支持的压缩编码：{encoding}')
    # Mock/in-memory HTTP responses may already be decoded. Network streams
    # use bounded raw reads and bounded zlib output, before allocation.
    decoder = zlib.decompressobj(16+zlib.MAX_WBITS) if encoding == 'gzip' and not response.is_stream_consumed else None
    chunks = [response.content] if response.is_stream_consumed else response.iter_raw(chunk_size=65536)
    transferred = 0
    for chunk in chunks:
        transferred += len(chunk)
        if transferred > limit:
            raise ValueError('HTTP响应超过传输大小限制')
        try:
            decoded = decoder.decompress(chunk, max(1, limit+1-len(data))) if decoder else chunk
        except zlib.error as error:
            raise httpx.DecodingError(f'HTTP gzip 响应无效：{error}') from error
        data.extend(decoded)
        if len(data) > limit or (decoder and decoder.unconsumed_tail):
            raise ValueError('HTTP响应超过响应大小限制')
    if decoder and (not decoder.eof or decoder.unused_data):
        raise httpx.DecodingError('HTTP gzip 响应不完整或包含多段压缩数据')
    return bytes(data)
