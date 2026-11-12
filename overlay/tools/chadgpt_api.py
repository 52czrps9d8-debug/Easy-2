# SPDX-License-Identifier: AGPL-3.0-or-later
"""Minimal client for the chad (chadgpt.ru) public generation API V2.

Documented flow (https://support.chadgpt.ru/?section=api, "chad Image API V2"):

1. ``POST {BASE_URL}/api/v2/public/{model_id}/imagine`` with a JSON body that
   carries ``api_key``, ``prompt``, ``aspect_ratio`` and model-specific fields.
   The answer holds a ``content_id`` (UUID).
2. ``POST {BASE_URL}/api/v2/public/check`` with ``api_key`` and ``content_id``
   until ``status`` is ``completed`` (``output`` is a list of URLs),
   ``failed`` or ``cancelled``.

Business errors come back with HTTP 200 and ``{"status": "failed",
"error_code": ..., "error_message": ...}``.

The key travels in the JSON body, not in a header, so it has to be present in
the process environment as ``CHADGPT_API_KEY`` (or in OpenMontage's ``.env``).
It is never included in error messages or results.
"""

from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

DEFAULT_BASE_URL = "https://ask.chadgpt.ru"
ENV_KEY = "CHADGPT_API_KEY"
ENV_BASE_URL = "CHADGPT_BASE_URL"

PENDING_STATUSES = {"starting", "pending", "processing", "queued", "running", "in_progress"}
FAILED_STATUSES = {"failed", "cancelled", "canceled", "error"}

# Requests that are safe to repeat (status checks, downloads) are retried on
# transport errors; the chad edge occasionally resets connections.
_SAFE_RETRIES = 3


class ChadApiError(RuntimeError):
    """An error reported by the chad API (or a malformed answer from it)."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code


def get_api_key() -> str | None:
    value = os.environ.get(ENV_KEY, "").strip()
    return value or None


def base_url() -> str:
    return (os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")


def _raise_if_failed(data: Any, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ChadApiError(f"unexpected {what} response: {type(data).__name__}")
    if str(data.get("status", "")).lower() in FAILED_STATUSES:
        raise ChadApiError(
            str(data.get("error_message") or data.get("detail") or f"{what} failed"),
            code=data.get("error_code"),
        )
    return data


def _json(resp: requests.Response, what: str) -> Any:
    if resp.status_code == 404:
        raise ChadApiError(f"{what}: endpoint or model not found (HTTP 404)", code="HTTP-404")
    if resp.status_code >= 400:
        detail = ""
        try:
            body = resp.json()
            detail = body.get("detail") or body.get("error_message") or ""
        except Exception:
            pass
        raise ChadApiError(
            f"{what}: HTTP {resp.status_code} {detail}".strip(),
            code=f"HTTP-{resp.status_code}",
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise ChadApiError(f"{what}: response is not JSON") from exc


def submit(model_id: str, payload: dict[str, Any], *, timeout: float = 60) -> dict[str, Any]:
    """Start a generation job. Not retried: a repeat could be billed twice."""
    key = get_api_key()
    if not key:
        raise ChadApiError(f"{ENV_KEY} is not set", code="NO-KEY")
    body = {k: v for k, v in payload.items() if v is not None}
    body["api_key"] = key
    resp = requests.post(
        f"{base_url()}/api/v2/public/{model_id}/imagine",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    data = _raise_if_failed(_json(resp, f"imagine[{model_id}]"), "imagine")
    if not data.get("content_id"):
        raise ChadApiError("imagine response has no content_id")
    return data


def check(content_id: str, *, timeout: float = 30) -> dict[str, Any]:
    """Return the raw status payload for a job (errors raise ChadApiError)."""
    key = get_api_key()
    if not key:
        raise ChadApiError(f"{ENV_KEY} is not set", code="NO-KEY")
    last_exc: Exception | None = None
    for attempt in range(_SAFE_RETRIES):
        try:
            resp = requests.post(
                f"{base_url()}/api/v2/public/check",
                json={"api_key": key, "content_id": content_id},
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            return _raise_if_failed(_json(resp, "check"), "check")
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(min(2 ** attempt, 8))
    raise ChadApiError(f"check: network error after {_SAFE_RETRIES} attempts: {last_exc}")


def wait_for_output(
    content_id: str,
    *,
    poll_interval: float = 5,
    timeout: float = 600,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> list[str]:
    """Poll ``check`` until the job finishes; return the output URLs."""
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + timeout
    while True:
        data = check(content_id)
        status = str(data.get("status", "")).lower()
        if status == "completed":
            output = data.get("output") or []
            if isinstance(output, str):
                output = [output]
            urls = [u for u in output if isinstance(u, str) and u]
            if not urls:
                raise ChadApiError("job completed without output URLs")
            return urls
        if status and status not in PENDING_STATUSES:
            raise ChadApiError(f"unexpected job status {status!r}")
        if clock() >= deadline:
            raise ChadApiError(
                f"job {content_id} still {status or 'pending'} after {int(timeout)}s; "
                "it may still finish — check it again later with the same content_id",
                code="TIMEOUT",
            )
        sleep(poll_interval)


_CT_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}


def guess_extension(url: str, content_type: str | None, default: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _CT_EXT:
        return _CT_EXT[ct]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix and mimetypes.guess_type(f"x{suffix}")[0]:
        return ".jpg" if suffix == ".jpeg" else suffix
    return default


def download(url: str, dest: Path, *, default_ext: str, timeout: float = 300) -> Path:
    """Download ``url`` to ``dest``; fix the suffix if the real type differs."""
    last_exc: Exception | None = None
    for attempt in range(_SAFE_RETRIES):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(min(2 ** attempt, 8))
    else:
        raise ChadApiError(f"download failed after {_SAFE_RETRIES} attempts: {last_exc}")
    ext = guess_extension(url, resp.headers.get("Content-Type"), default_ext)
    dest = Path(dest)
    if dest.suffix.lower() != ext and not (dest.suffix.lower() in {".jpg", ".jpeg"} and ext == ".jpg"):
        dest = dest.with_suffix(ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest


def numbered_path(base: Path, index: int) -> Path:
    """``out.png`` -> ``out.png`` (index 0), ``out_2.png`` (index 1), ..."""
    if index == 0:
        return base
    return base.with_name(f"{base.stem}_{index + 1}{base.suffix}")


def ratio_value(ratio: str) -> float:
    w, _, h = ratio.partition(":")
    return float(w) / float(h)


def closest_ratio(target: float, allowed: list[str]) -> str:
    import math

    return min(allowed, key=lambda r: abs(math.log(ratio_value(r)) - math.log(target)))
