# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline tests for the chad (chadgpt.ru) image/video adapters.

All HTTP calls are mocked; no key or network access is needed.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools import chadgpt_api as chad  # noqa: E402
from tools.base_tool import ToolStatus  # noqa: E402
from tools.graphics.chadgpt_image import ChadgptImage  # noqa: E402
from tools.video.chadgpt_video import ChadgptVideo  # noqa: E402

KEY = "test-chad-key"


class FakeResp:
    def __init__(self, json_data=None, status=200, content=b"", headers=None):
        self._json = json_data
        self.status_code = status
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)


class FakeApi:
    """Records calls and plays back scripted responses."""

    def __init__(self, imagine, checks, files=None):
        self.imagine = imagine
        self.checks = list(checks)
        self.files = files or {}
        self.posts = []
        self.gets = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, dict(json or {})))
        if url.endswith("/imagine"):
            return self.imagine
        if url.endswith("/check"):
            return self.checks.pop(0)
        raise AssertionError(url)

    def get(self, url, timeout=None):
        self.gets.append(url)
        content, ctype = self.files[url]
        return FakeResp(content=content, headers={"Content-Type": ctype})


@pytest.fixture
def api(monkeypatch):
    def install(imagine, checks, files=None):
        fake = FakeApi(imagine, checks, files)
        monkeypatch.setattr(chad.requests, "post", fake.post)
        monkeypatch.setattr(chad.requests, "get", fake.get)
        monkeypatch.setattr(chad.time, "sleep", lambda s: None)
        return fake

    return install


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("CHADGPT_API_KEY", KEY)
    monkeypatch.delenv("CHADGPT_BASE_URL", raising=False)
    monkeypatch.delenv("CHADGPT_VIDEO_ENABLED", raising=False)


def started(cid="11111111-1111-1111-1111-111111111111"):
    return FakeResp({"status": "starting", "content_id": cid})


# ---------------------------------------------------------------- image


def test_image_happy_path(api, tmp_path):
    url = "https://cdn.example/out/abc.png"
    fake = api(
        started(),
        [FakeResp({"status": "pending"}), FakeResp({"status": "completed", "output": [url]})],
        {url: (b"PNG", "image/png")},
    )
    out = tmp_path / "shot.png"
    res = ChadgptImage().execute({"prompt": "a red fox", "aspect_ratio": "16:9", "output_path": str(out)})
    assert res.success, res.error
    assert res.artifacts == [str(out)] and out.read_bytes() == b"PNG"
    imagine_url, body = fake.posts[0]
    assert imagine_url == "https://ask.chadgpt.ru/api/v2/public/flux-2-pro/imagine"
    assert body == {"prompt": "a red fox", "aspect_ratio": "16:9", "api_key": KEY}
    assert fake.posts[1][1] == {"api_key": KEY, "content_id": res.data["content_id"]}
    assert res.cost_usd is None
    assert KEY not in repr(res.data)


def test_image_business_error_returned_with_http_200(api):
    api(FakeResp({"status": "failed", "error_code": "IMG-001", "error_message": "invalid-api-key"}), [])
    res = ChadgptImage().execute({"prompt": "x"})
    assert not res.success
    assert "IMG-001" in res.error and "invalid-api-key" in res.error
    assert KEY not in res.error


def test_image_job_failure_reports_content_id(api):
    api(started("cid-1"), [FakeResp({"status": "failed", "error_code": "IMG-500", "error_message": "nsfw"})])
    res = ChadgptImage().execute({"prompt": "x"})
    assert not res.success and "cid-1" in res.error and "nsfw" in res.error


def test_image_timeout(api, monkeypatch):
    api(started("cid-2"), [FakeResp({"status": "pending"})] * 50)
    ticks = iter(range(0, 10_000, 100))
    monkeypatch.setattr(chad.time, "monotonic", lambda: next(ticks))
    res = ChadgptImage().execute({"prompt": "x", "timeout_seconds": 250})
    assert not res.success and "TIMEOUT" in res.error and "cid-2" in res.error


def test_midjourney_multiple_outputs_and_svg_suffix(api, tmp_path):
    urls = [f"https://cdn.example/{i}.jpg" for i in range(4)]
    api(started(), [FakeResp({"status": "completed", "output": urls})],
        {u: (b"J", "image/jpeg") for u in urls})
    res = ChadgptImage().execute({"prompt": "x", "model": "mj-7", "output_path": str(tmp_path / "m.png")})
    assert res.success, res.error
    assert [Path(p).name for p in res.artifacts] == ["m.jpg", "m_2.jpg", "m_3.jpg", "m_4.jpg"]

    svg = "https://cdn.example/v.svg"
    api(started(), [FakeResp({"status": "completed", "output": [svg]})], {svg: (b"<svg/>", "image/svg+xml")})
    res = ChadgptImage().execute({"prompt": "x", "model": "recraft-v4-svg", "output_path": str(tmp_path / "v.png")})
    assert res.success and res.artifacts == [str(tmp_path / "v.svg")]


def test_http_404_is_clear(api):
    api(FakeResp({"detail": "Not Found"}, status=404), [])
    res = ChadgptImage().execute({"prompt": "x", "model": "imagen-4"})
    assert not res.success and "404" in res.error


# ---------------------------------------------------------------- payload rules


def test_aspect_ratio_snaps_to_supported():
    _, payload, notes = ChadgptImage().build_payload({"prompt": "x", "model": "gpt-img-2", "aspect_ratio": "16:9"})
    assert payload["aspect_ratio"] == "3:2" and notes


def test_width_height_derive_ratio():
    _, payload, _ = ChadgptImage().build_payload({"prompt": "x", "aspect_ratio": None, "width": 1080, "height": 1920})
    assert payload["aspect_ratio"] == "9:16"


def test_reference_images(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG")
    _, p, _ = ChadgptImage().build_payload(
        {"prompt": "x", "model": "flux-2-pro", "image_url": "https://a/1.png", "image_path": str(img)}
    )
    assert p["image_urls"] == ["https://a/1.png"] and p["image_base64s"] == ["iVBORw=="]

    _, p, _ = ChadgptImage().build_payload({"prompt": "x", "model": "flux-kontext-pro", "image_url": "https://a/1.png"})
    assert p["image_url"] == "https://a/1.png" and "image_urls" not in p

    with pytest.raises(ValueError, match="only image URLs"):
        ChadgptImage().build_payload({"prompt": "x", "model": "seedream-4.5", "image_path": str(img)})
    with pytest.raises(ValueError, match="at most"):
        ChadgptImage().build_payload({"prompt": "x", "model": "gpt-img-2", "image_urls": ["u"] * 6})


def test_size_seed_images_and_extra_params():
    _, p, notes = ChadgptImage().build_payload(
        {"prompt": "x", "model": "gemini-3-pro-image", "resolution": "4k", "seed": 5}
    )
    assert p["size_preset"] == "4K" and "seed" not in p and any("seed" in n for n in notes)
    _, p, _ = ChadgptImage().build_payload({"prompt": "x", "model": "flux-1-schnell", "n": 4})
    assert p["images"] == 4
    _, p, _ = ChadgptImage().build_payload(
        {"prompt": "x", "model": "mj-7", "seed": 3, "extra_params": {"stylize": 200, "api_key": "evil"}}
    )
    assert p["seed"] == 3 and p["stylize"] == 200 and "api_key" not in p


def test_unknown_model_and_long_prompt():
    with pytest.raises(ValueError):
        ChadgptImage().build_payload({"prompt": "x", "model": "nope"})
    with pytest.raises(ValueError):
        ChadgptImage().build_payload({"prompt": "x" * 4001})


def test_status_and_missing_key(monkeypatch):
    assert ChadgptImage().get_status() == ToolStatus.AVAILABLE
    monkeypatch.delenv("CHADGPT_API_KEY")
    assert ChadgptImage().get_status() == ToolStatus.UNAVAILABLE
    res = ChadgptImage().execute({"prompt": "x"})
    assert not res.success and "CHADGPT_API_KEY" in res.error


def test_registry_discovers_both_tools():
    from tools.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    names = {t.name for t in reg.get_by_capability("image_generation")}
    assert "chadgpt_image" in names
    assert "chadgpt_video" in {t.name for t in reg.get_by_capability("video_generation")}


# ---------------------------------------------------------------- video


def test_video_needs_opt_in(monkeypatch):
    assert ChadgptVideo().get_status() == ToolStatus.UNAVAILABLE
    monkeypatch.setenv("CHADGPT_VIDEO_ENABLED", "1")
    assert ChadgptVideo().get_status() == ToolStatus.AVAILABLE


def test_video_happy_path(api, tmp_path):
    url = "https://cdn.example/clip"
    fake = api(started(), [FakeResp({"status": "completed", "output": [url]})], {url: (b"MP4", "video/mp4")})
    out = tmp_path / "clip.mp4"
    res = ChadgptVideo().execute(
        {"prompt": "waves", "aspect_ratio": "9:16", "duration": 5, "image_url": "https://a/f.png",
         "output_path": str(out)}
    )
    assert res.success, res.error
    assert out.read_bytes() == b"MP4"
    url0, body = fake.posts[0]
    assert url0.endswith("/api/v2/public/seedance-2.0/imagine")
    assert body == {"prompt": "waves", "aspect_ratio": "9:16", "duration": 5,
                    "image_urls": ["https://a/f.png"], "api_key": KEY}
    assert res.data["experimental"] is True
