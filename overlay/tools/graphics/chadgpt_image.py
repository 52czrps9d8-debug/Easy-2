# SPDX-License-Identifier: AGPL-3.0-or-later
"""Image generation through the chad (chadgpt.ru) Image API V2.

One key (``CHADGPT_API_KEY``) reaches ~30 models: FLUX 1/2/Kontext, GPT Image,
Gemini image (Nano Banana), Imagen 4, Midjourney, Seedream, Recraft (SVG) and
Stable Diffusion. Billing is in rubles from the chad account balance, so the
USD price is not known in advance (``estimate_cost`` asks for a quote).

Model parameters below follow the chad Image API V2 documentation.
"""

from __future__ import annotations

import base64
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

_IMAGEN = ["1:1", "16:9", "9:16", "4:3", "3:4"]
_GEMINI = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
_MJ = ["1:1", "3:2", "2:3", "16:9", "9:16"]
_FLUX_SCHNELL = ["1:1", "7:4", "4:7", "19:13", "13:19", "9:7", "7:9"]
_FLUX_11_PRO = ["1:1", "7:4", "4:7", "19:13", "13:19", "17:14", "14:17"]
_FLUX_ULTRA = ["1:1", "21:9", "16:9", "3:2", "4:3", "5:4", "4:5", "3:4", "2:3", "9:16", "9:21"]
_FLUX_2 = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "21:9", "9:21",
           "5:4", "4:5", "7:4", "4:7"]
_KONTEXT = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "4:5", "5:4", "21:9", "9:21",
            "2:1", "1:2"]
_RECRAFT_3 = ["1:1", "4:3", "16:9", "3:2", "2:1"]
_RECRAFT_4 = ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "2:1"]
_GPT = ["1:1", "3:2", "2:3"]
_SEEDREAM = ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9"]
_SD = ["1:1", "9:7", "7:9", "19:13", "7:4", "4:7", "12:5", "5:12"]


def _spec(ratios, *, urls=0, b64=0, single_url=False, seed=False, sizes=None,
          images=None, fields=(), outputs=1):
    return {
        "ratios": ratios,
        "max_urls": urls,          # image_urls
        "max_b64": b64,            # image_base64s
        "single_url": single_url,  # image_url (Kontext)
        "seed": seed,
        "sizes": sizes,            # size_preset values, first = default
        "images": images,          # allowed "images" counts
        "fields": set(fields),     # other model-specific passthrough fields
        "outputs": outputs,        # images returned per request when fixed
    }


# Ordered roughly from "good default" to niche.
MODELS: dict[str, dict[str, Any]] = {
    "flux-2-pro": _spec(_FLUX_2, urls=5, b64=5, seed=True),
    "flux-2-flex": _spec(_FLUX_2, urls=5, b64=5, seed=True, fields=("optimize",)),
    "gpt-img-2": _spec(_GPT, urls=5, b64=5),
    "gpt-img-1.5": _spec(_GPT, urls=5, b64=5, fields=("quality",)),
    "gpt-img-low": _spec(_GPT, urls=5, b64=5),
    "gpt-img-medium": _spec(_GPT, urls=5, b64=5),
    "gpt-img-high": _spec(_GPT, urls=5, b64=5),
    "gemini-3-pro-image": _spec(_GEMINI, urls=5, b64=5, sizes=["2K", "1K", "4K"]),
    "gemini-3.1-flash-image-preview": _spec(_GEMINI, urls=14, b64=14, sizes=["1K", "2K", "4K"]),
    "gemini-2.5-flash-image": _spec(_GEMINI, urls=5, b64=5),
    "seedream-4.5": _spec(_SEEDREAM, urls=5, sizes=["2K", "1K", "4K"]),
    "seedream-4": _spec(_SEEDREAM, urls=5, sizes=["2K", "1K", "4K"]),
    "seedream-5-lite": _spec(_SEEDREAM, urls=14, sizes=["2K", "3K"]),
    "flux-1.1-pro-ultra": _spec(_FLUX_ULTRA, seed=True, fields=("is_raw",)),
    "flux-1.1-pro": _spec(_FLUX_11_PRO),
    "flux-1-schnell": _spec(_FLUX_SCHNELL, images=[1, 4]),
    "flux-kontext-pro": _spec(_KONTEXT, single_url=True, seed=True),
    "flux-kontext-max": _spec(_KONTEXT, single_url=True, seed=True),
    "imagen-4": _spec(_IMAGEN),
    "imagen-4-fast": _spec(_IMAGEN),
    "imagen-4-ultra": _spec(_IMAGEN),
    "mj-7": _spec(_MJ, fields=("chaos", "no", "quality", "seed", "stop", "style",
                               "stylize", "tile", "weird"), outputs=4),
    "mj-6.1": _spec(_MJ, fields=("chaos", "no", "quality", "seed", "stop", "style",
                                 "stylize", "tile", "weird"), outputs=4),
    "mj-6": _spec(_MJ, fields=("chaos", "no", "quality", "seed", "stop", "style",
                               "stylize", "tile", "weird"), outputs=4),
    "mj-5.2": _spec(_MJ, fields=("chaos", "no", "quality", "seed", "stop", "style",
                                 "stylize", "tile", "weird"), outputs=4),
    "recraft-v4-pro-svg": _spec(_RECRAFT_4),
    "recraft-v4-svg": _spec(_RECRAFT_4),
    "recraft-v3-svg": _spec(_RECRAFT_3, fields=("style",)),
    "stdiff-m": _spec(_SD, images=[1, 4]),
}
DEFAULT_MODEL = "flux-2-pro"


def _file_to_b64(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Input image not found: {p}")
    return base64.b64encode(p.read_bytes()).decode("ascii")


class ChadgptImage(BaseTool):
    name = "chadgpt_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "chadgpt"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:CHADGPT_API_KEY"]
    install_instructions = (
        "Set CHADGPT_API_KEY to a chad API key (ask.chadgpt.ru -> profile -> Chad API).\n"
        "  The account balance must be topped up. Network access to ask.chadgpt.ru\n"
        "  and to the CDN that serves the results is required."
    )
    agent_skills = ["flux-best-practices"]

    capabilities = ["generate_image", "text_to_image", "image_edit", "generate_illustration"]
    supports = {
        "seed": True,
        "aspect_ratio": True,
        "reference_images": True,
        "image_edit": True,
        "vector_svg": True,
        "multiple_models": True,
    }
    best_for = [
        "one key for FLUX 2, GPT Image 2, Nano Banana, Imagen 4, Midjourney, Seedream, Recraft",
        "paying in rubles with a Russian bank card",
        "reference-image editing (FLUX 2, GPT Image, Gemini, Seedream, Kontext)",
        "vector SVG illustrations (Recraft models)",
    ]
    not_good_for = [
        "exact pixel sizes (only aspect ratios are supported)",
        "offline generation",
        "budget gating in USD (prices are in rubles; quote before large batches)",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
            "model": {"type": "string", "enum": list(MODELS), "default": DEFAULT_MODEL},
            "aspect_ratio": {
                "type": "string",
                "description": "W:H. Snapped to the closest ratio the model supports.",
                "default": "16:9",
            },
            "width": {"type": "integer", "description": "Used only to derive aspect_ratio."},
            "height": {"type": "integer", "description": "Used only to derive aspect_ratio."},
            "resolution": {
                "type": "string",
                "description": "size_preset for Gemini 3 / Seedream models (1K, 2K, 3K, 4K).",
            },
            "seed": {"type": "integer"},
            "n": {"type": "integer", "description": "1 or 4 for flux-1-schnell / stdiff-m."},
            "image_url": {"type": "string"},
            "image_urls": {"type": "array", "items": {"type": "string"}},
            "image_path": {"type": "string"},
            "image_paths": {"type": "array", "items": {"type": "string"}},
            "extra_params": {
                "type": "object",
                "description": "Model-specific fields passed through as-is "
                               "(e.g. stylize/chaos for Midjourney, style for recraft-v3-svg).",
            },
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "minimum": 1, "default": 4},
            "timeout_seconds": {"type": "number", "minimum": 30, "default": 600},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=100, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = [
        "prompt", "model", "aspect_ratio", "width", "height", "resolution", "seed", "n",
        "image_url", "image_urls", "image_path", "image_paths", "extra_params",
    ]
    side_effects = ["writes image file(s) next to output_path", "calls chad Image API (paid)"]
    user_visible_verification = ["Inspect generated image for relevance and quality"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if chad.get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        from tools.provider_pricing import PriceQuoteRequired

        raise PriceQuoteRequired(
            "chad bills in rubles from the account balance; check the model price "
            "at api.chadgpt.ru before generating"
        )

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 60.0 if str(inputs.get("model", "")).startswith("mj-") else 25.0

    # ---- payload -------------------------------------------------------

    def build_payload(self, inputs: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
        """Return (model_id, payload_without_key, notes)."""
        model = inputs.get("model") or DEFAULT_MODEL
        if model not in MODELS:
            raise ValueError(f"Unknown chad image model {model!r}. Known: {', '.join(MODELS)}")
        spec = MODELS[model]
        notes: list[str] = []
        prompt = str(inputs.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("prompt is required")
        if len(prompt) > 4000:
            raise ValueError("prompt is longer than 4000 characters")

        ratio = inputs.get("aspect_ratio")
        if not ratio and inputs.get("width") and inputs.get("height"):
            target = float(inputs["width"]) / float(inputs["height"])
        elif ratio:
            target = chad.ratio_value(str(ratio))
        else:
            target = 16 / 9
        if ratio in spec["ratios"]:
            chosen = ratio
        else:
            chosen = chad.closest_ratio(target, spec["ratios"])
            notes.append(f"aspect_ratio {ratio or f'{target:.3f}'} -> {chosen} (closest supported)")

        payload: dict[str, Any] = {"prompt": prompt, "aspect_ratio": chosen}

        if spec["sizes"]:
            size = inputs.get("resolution")
            if size and str(size).upper() in spec["sizes"]:
                payload["size_preset"] = str(size).upper()
            elif size:
                notes.append(f"resolution {size} unsupported; using {spec['sizes'][0]}")
                payload["size_preset"] = spec["sizes"][0]

        if inputs.get("seed") is not None:
            if spec["seed"] or "seed" in spec["fields"]:
                payload["seed"] = int(inputs["seed"])
            else:
                notes.append("seed ignored: not supported by this model")

        if spec["images"]:
            n = int(inputs.get("n") or 1)
            payload["images"] = 4 if n >= 4 else 1

        urls = list(inputs.get("image_urls") or [])
        if inputs.get("image_url"):
            urls.insert(0, inputs["image_url"])
        paths = list(inputs.get("image_paths") or [])
        if inputs.get("image_path"):
            paths.insert(0, inputs["image_path"])

        if urls or paths:
            if spec["single_url"]:
                if paths or len(urls) != 1:
                    raise ValueError(f"{model} takes exactly one reference image URL (image_url)")
                payload["image_url"] = urls[0]
            else:
                if len(urls) > spec["max_urls"]:
                    raise ValueError(f"{model} accepts at most {spec['max_urls']} image URLs")
                if paths and not spec["max_b64"]:
                    raise ValueError(
                        f"{model} accepts only image URLs, not local files; "
                        "upload the image and pass image_url(s)"
                    )
                if len(paths) > spec["max_b64"]:
                    raise ValueError(f"{model} accepts at most {spec['max_b64']} local images")
                if urls:
                    payload["image_urls"] = urls
                if paths:
                    payload["image_base64s"] = [_file_to_b64(p) for p in paths]

        extra = dict(inputs.get("extra_params") or {})
        for key in ("api_key", "prompt", "aspect_ratio"):
            extra.pop(key, None)
        payload.update(extra)
        return model, payload, notes

    # ---- run -----------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if not chad.get_api_key():
            return ToolResult(success=False, error="CHADGPT_API_KEY not set. " + self.install_instructions)
        start = time.time()
        try:
            model, payload, notes = self.build_payload(inputs)
        except (ValueError, FileNotFoundError) as exc:
            return ToolResult(success=False, error=f"chadgpt_image: {exc}")

        content_id = None
        try:
            job = chad.submit(model, payload)
            content_id = job["content_id"]
            urls = chad.wait_for_output(
                content_id,
                poll_interval=float(inputs.get("poll_interval_seconds", 4)),
                timeout=float(inputs.get("timeout_seconds", 600)),
            )
            base = Path(inputs.get("output_path") or "generated_image.png")
            saved = [
                str(chad.download(u, chad.numbered_path(base, i), default_ext=base.suffix or ".png"))
                for i, u in enumerate(urls)
            ]
        except chad.ChadApiError as exc:
            where = f" (content_id={content_id})" if content_id else ""
            return ToolResult(success=False, error=f"chad image generation failed{where}: {exc}")
        except Exception as exc:  # network / filesystem
            where = f" (content_id={content_id})" if content_id else ""
            return ToolResult(success=False, error=f"chad image generation failed{where}: {exc}")

        data: dict[str, Any] = {
            "provider": "chadgpt",
            "model": model,
            "prompt": payload["prompt"],
            "aspect_ratio": payload["aspect_ratio"],
            "content_id": content_id,
            "output": saved[0],
            "outputs": saved,
            "source_urls": urls,
        }
        if notes:
            data["notes"] = notes
        return ToolResult(
            success=True,
            data=data,
            artifacts=saved,
            cost_usd=None,
            duration_seconds=round(time.time() - start, 2),
            seed=payload.get("seed"),
            model=f"chadgpt/{model}",
        )
