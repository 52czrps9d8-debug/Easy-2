# SPDX-License-Identifier: AGPL-3.0-or-later
"""EXPERIMENTAL video generation through the chad (chadgpt.ru) API V2.

chad documents only image models for its public API, but the same
``/api/v2/public/{model}/imagine`` route also answers for some video models
(``seedance-2.0``, ``kling-2.6-pro``, ``grok-imagine-video`` reply
"invalid-api-key" rather than 404 when probed without a key). Their extra
parameters (duration, resolution, source frame) are NOT documented, so:

* only ``prompt`` and ``aspect_ratio`` are sent by default;
* ``duration`` / reference images are sent only when given, under guessed field
  names (``duration``, ``image_urls``) — the API may ignore or reject them;
* anything else goes through ``extra_params`` verbatim.

The tool reports ``unavailable`` unless ``CHADGPT_API_KEY`` is set AND
``CHADGPT_VIDEO_ENABLED=1``, so automatic provider selection never spends
money on it until a human has tried it once and opted in.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from tools import chadgpt_api as chad
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

VIDEO_MODELS = ["seedance-2.0", "kling-2.6-pro", "grok-imagine-video"]
DEFAULT_MODEL = "seedance-2.0"
RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
ENV_ENABLE = "CHADGPT_VIDEO_ENABLED"


def video_enabled() -> bool:
    return os.environ.get(ENV_ENABLE, "").strip().lower() in {"1", "true", "yes", "on"}


class ChadgptVideo(BaseTool):
    name = "chadgpt_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "chadgpt"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:CHADGPT_API_KEY", f"env:{ENV_ENABLE}"]
    install_instructions = (
        "Set CHADGPT_API_KEY to a chad API key and CHADGPT_VIDEO_ENABLED=1.\n"
        "  Video on the chad API is undocumented: run one short test clip by hand\n"
        "  before letting the pipeline use it."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video"]
    supports = {"text_to_video": True, "image_to_video": True, "aspect_ratio": True}
    best_for = [
        "Seedance 2.0 / Kling 2.6 Pro / Grok Imagine video paid in rubles via one chad key",
    ]
    not_good_for = [
        "precise control of duration/resolution (parameters are undocumented)",
        "unattended batch generation before a manual test run",
        "offline generation",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
            "model": {"type": "string", "enum": VIDEO_MODELS, "default": DEFAULT_MODEL},
            "aspect_ratio": {"type": "string", "default": "16:9"},
            "duration": {"type": "integer", "description": "Seconds; sent as 'duration' (unverified)."},
            "image_url": {"type": "string", "description": "Start frame URL; sent as image_urls (unverified)."},
            "image_urls": {"type": "array", "items": {"type": "string"}},
            "extra_params": {"type": "object", "description": "Raw fields passed to the API as-is."},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "minimum": 2, "default": 10},
            "timeout_seconds": {"type": "number", "minimum": 60, "default": 1200},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = ["prompt", "model", "aspect_ratio", "duration",
                              "image_url", "image_urls", "extra_params"]
    side_effects = ["writes video file to output_path", "calls chad API (paid)"]
    user_visible_verification = ["Watch the clip: check motion, duration and aspect ratio"]

    def get_status(self) -> ToolStatus:
        if chad.get_api_key() and video_enabled():
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        from tools.provider_pricing import PriceQuoteRequired

        raise PriceQuoteRequired(
            "chad bills video in rubles from the account balance; check the price first"
        )

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 180.0

    def build_payload(self, inputs: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
        model = inputs.get("model") or DEFAULT_MODEL
        if model not in VIDEO_MODELS:
            raise ValueError(f"Unknown chad video model {model!r}. Known: {', '.join(VIDEO_MODELS)}")
        prompt = str(inputs.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("prompt is required")
        if len(prompt) > 4000:
            raise ValueError("prompt is longer than 4000 characters")
        notes: list[str] = []
        ratio = inputs.get("aspect_ratio") or "16:9"
        if ratio not in RATIOS:
            snapped = chad.closest_ratio(chad.ratio_value(ratio), RATIOS)
            notes.append(f"aspect_ratio {ratio} -> {snapped}")
            ratio = snapped
        payload: dict[str, Any] = {"prompt": prompt, "aspect_ratio": ratio}
        if inputs.get("duration"):
            payload["duration"] = int(inputs["duration"])
        urls = list(inputs.get("image_urls") or [])
        if inputs.get("image_url"):
            urls.insert(0, inputs["image_url"])
        if urls:
            payload["image_urls"] = urls
        extra = dict(inputs.get("extra_params") or {})
        extra.pop("api_key", None)
        payload.update(extra)
        return model, payload, notes

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if not chad.get_api_key():
            return ToolResult(success=False, error="CHADGPT_API_KEY not set. " + self.install_instructions)
        start = time.time()
        try:
            model, payload, notes = self.build_payload(inputs)
        except ValueError as exc:
            return ToolResult(success=False, error=f"chadgpt_video: {exc}")

        content_id = None
        try:
            job = chad.submit(model, payload)
            content_id = job["content_id"]
            urls = chad.wait_for_output(
                content_id,
                poll_interval=float(inputs.get("poll_interval_seconds", 10)),
                timeout=float(inputs.get("timeout_seconds", 1200)),
            )
            out = chad.download(
                urls[0],
                Path(inputs.get("output_path") or "generated_video.mp4"),
                default_ext=".mp4",
            )
        except Exception as exc:
            where = f" (content_id={content_id})" if content_id else ""
            return ToolResult(success=False, error=f"chad video generation failed{where}: {exc}")

        data: dict[str, Any] = {
            "provider": "chadgpt",
            "model": model,
            "prompt": payload["prompt"],
            "content_id": content_id,
            "output": str(out),
            "source_urls": urls,
            "sent_fields": sorted(payload),
            "experimental": True,
        }
        try:
            from tools.video._shared import probe_output

            data["probe"] = probe_output(out)
        except Exception:
            pass
        if notes:
            data["notes"] = notes
        return ToolResult(
            success=True,
            data=data,
            artifacts=[str(out)],
            cost_usd=None,
            duration_seconds=round(time.time() - start, 2),
            model=f"chadgpt/{model}",
        )
