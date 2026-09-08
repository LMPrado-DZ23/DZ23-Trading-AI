"""Bound remote response bodies before JSON/CSV materialization."""

from __future__ import annotations

from typing import Any


def read_bounded_response(response: Any, *, max_bytes: int, vendor: str) -> bytes:
    if max_bytes <= 0:
        raise ValueError("response byte limit is invalid")
    headers = getattr(response, "headers", {}) or {}
    raw_length = headers.get("content-length") or headers.get("Content-Length")
    if raw_length is not None:
        try:
            if int(raw_length) > max_bytes:
                raise ValueError(f"{vendor} response exceeds the byte limit")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "exceeds" in str(exc):
                raise
            raise ValueError(f"{vendor} returned an invalid Content-Length") from None

    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks: list[bytes] = []
        total = 0
        for chunk in iterator(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{vendor} response exceeds the byte limit")
            chunks.append(bytes(chunk))
        return b"".join(chunks)

    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", "")).encode("utf-8")
    elif isinstance(content, str):
        content = content.encode("utf-8")
    else:
        content = bytes(content)
    if len(content) > max_bytes:
        raise ValueError(f"{vendor} response exceeds the byte limit")
    return content
