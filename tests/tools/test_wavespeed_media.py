"""Offline request, routing and resume contracts for the WaveSpeed image/video tools."""

import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

import tools.schema_media as schema_media
from tools.graphics.wavespeed_image import WaveSpeedImage
from tools.video.wavespeed_video import WaveSpeedVideo

API = "https://api.wavespeed.ai/api/v3/"
VIDEO = "data:video/mp4;base64,dmlkZW8="
IMAGE = "data:image/png;base64,aW1hZ2U="


def _envelope(data, code=200, message="success"):
    return {"code": code, "message": message, "data": data}


def _fake_api(monkeypatch, *, submission=None, results=()):
    """Patch request_json; return the list of (method, url, kwargs) calls."""
    calls = []
    pending = list(results)

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return submission or _envelope({"id": "pred-1", "status": "created", "outputs": []})
        return pending.pop(0) if len(pending) > 1 else pending[0]

    monkeypatch.setattr(schema_media, "request_json", request)
    monkeypatch.setattr(schema_media, "poll", _fast_poll)
    return calls


def _fast_poll(fetch, *, timeout=600, interval=2):
    from tools.provider_jobs import poll

    return poll(fetch, timeout=timeout, interval=0.001)


@pytest.mark.parametrize("tool", [WaveSpeedImage, WaveSpeedVideo])
def test_tools_have_valid_contracts(tool, monkeypatch):
    instance = tool()
    Draft202012Validator.check_schema(instance.input_schema)
    assert instance.provider == "wavespeed"
    assert instance.agent_skills
    assert instance.retry_policy.max_retries == 0
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    assert instance.get_status().value == "unavailable"
    monkeypatch.setenv("WAVESPEED_API_KEY", "test")
    assert instance.get_status().value == "available"


def test_registry_discovers_both_tools():
    from tools.tool_registry import registry

    registry.discover()
    assert registry.get("wavespeed_image").capability == "image_generation"
    assert registry.get("wavespeed_video").capability == "video_generation"


def test_pinned_routes_match_contract_file():
    for tool in (WaveSpeedImage(), WaveSpeedVideo()):
        assert tool.endpoints <= set(tool.contracts)


def test_image_routes_and_references(tmp_path):
    tool = WaveSpeedImage()
    _, mode, endpoint, payload = tool.build_request(
        {"prompt": "A lighthouse", "aspect_ratio": "16:9", "resolution": "2k"}
    )
    assert (mode, endpoint) == ("generate", "bytedance/seedream-v5.5")
    assert payload == {"prompt": "A lighthouse", "aspect_ratio": "16:9", "resolution": "2k"}

    local = tmp_path / "ref.png"
    local.write_bytes(b"png")
    _, mode, endpoint, payload = tool.build_request(
        {
            "prompt": "Make it night",
            "image_url": "https://example.com/a.png",
            "image_paths": [str(local)],
        }
    )
    assert (mode, endpoint) == ("edit", "bytedance/seedream-v5.5/edit")
    assert payload["images"][0] == "https://example.com/a.png"
    assert payload["images"][1].startswith("data:image/png;base64,")


def test_video_routes_use_native_field_names():
    tool = WaveSpeedVideo()
    _, operation, endpoint, payload = tool.build_request(
        {
            "prompt": "Waves at dusk",
            "operation": "text_to_video",
            "duration": 8,
            "resolution": "1080p",
            "aspect_ratio": "9:16",
            "generate_audio": False,
            "seed": 7,
        }
    )
    assert (operation, endpoint) == ("text_to_video", "alibaba/wan-3.0/text-to-video")
    assert payload == {
        "prompt": "Waves at dusk",
        "duration": 8,
        "resolution": "1080p",
        "aspect_ratio": "9:16",
        "generate_audio": False,
        "seed": 7,
    }
    _, operation, endpoint, payload = tool.build_request(
        {"prompt": "Pan right", "image_url": "https://example.com/frame.png"}
    )
    assert (operation, endpoint) == ("image_to_video", "alibaba/wan-3.0/image-to-video")
    assert payload["image"] == "https://example.com/frame.png"


def test_invalid_parameters_rejected_before_submission():
    with pytest.raises(ValidationError):
        WaveSpeedVideo().build_request({"prompt": "x", "duration": 31})
    with pytest.raises(ValidationError):
        WaveSpeedVideo().build_request({"prompt": "x", "resolution": "4k"})
    with pytest.raises(ValidationError):
        WaveSpeedImage().build_request({"prompt": "x", "provider_params": {"invented": 1}})
    with pytest.raises(ValidationError):
        WaveSpeedVideo().build_request({"operation": "image_to_video", "prompt": "x"})
    with pytest.raises(ValueError):
        WaveSpeedVideo().build_request({"prompt": "x", "operation": "reference_to_video"})


def test_image_submit_poll_download(monkeypatch, tmp_path):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-key")
    calls = _fake_api(
        monkeypatch,
        results=[
            _envelope({"id": "pred-1", "status": "processing", "outputs": []}),
            _envelope({"id": "pred-1", "status": "completed", "outputs": [IMAGE]}),
        ],
    )
    out = tmp_path / "still.png"
    result = WaveSpeedImage().execute({"prompt": "A lighthouse", "output_path": str(out)})

    assert result.success, result.error
    assert out.read_bytes() == b"image"
    method, url, kwargs = calls[0]
    assert (method, url) == ("POST", API + "bytedance/seedream-v5.5")
    assert kwargs["json"] == {"prompt": "A lighthouse"}
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert [c[1] for c in calls[1:]] == [API + "predictions/pred-1/result"] * 2
    assert result.cost_usd is None and result.data["cost_status"] == "unquoted"
    assert result.data["resume_job"]["endpoint"] == "bytedance/seedream-v5.5"


def test_video_resume_does_not_submit_twice(monkeypatch, tmp_path):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test")
    calls = _fake_api(
        monkeypatch,
        results=[_envelope({"id": "pred-1", "status": "completed", "outputs": [VIDEO]})],
    )
    monkeypatch.setattr(
        schema_media, "poll", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("pending"))
    )
    tool = WaveSpeedVideo()
    job_path = tmp_path / "job.json"
    first = tool.execute({"prompt": "Clouds", "job_path": str(job_path)})
    assert not first.success and first.data["resume_job"]["id"] == "pred-1"
    assert json.loads(job_path.read_text()) == first.data["resume_job"]

    monkeypatch.setattr(schema_media, "poll", _fast_poll)
    out = tmp_path / "clip.mp4"
    again = tool.execute({"resume_job": first.data["resume_job"], "output_path": str(out)})
    assert again.success, again.error
    assert out.read_bytes() == b"video"
    assert sum(c[0] == "POST" for c in calls) == 1


def test_error_envelope_with_http_200_is_a_failure(monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test")
    calls = _fake_api(
        monkeypatch,
        submission=_envelope(None, code=400, message="prompt is required"),
        results=[_envelope({"status": "completed", "outputs": [IMAGE]})],
    )
    result = WaveSpeedImage().execute({"prompt": "x"})
    assert not result.success
    assert "code 400" in result.error and "prompt is required" in result.error
    assert [c[0] for c in calls] == ["POST"]
    assert "resume_job" not in result.data


def test_failed_prediction_reports_error_and_keeps_job(monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test")
    _fake_api(
        monkeypatch,
        results=[_envelope({"id": "pred-1", "status": "failed", "error": "content flagged"})],
    )
    result = WaveSpeedVideo().execute({"prompt": "x"})
    assert not result.success and "content flagged" in result.error
    assert result.data["resume_job"]["id"] == "pred-1"


def test_preflight_cost_is_not_reported_as_free(monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "test")
    for tool in (WaveSpeedImage(), WaveSpeedVideo()):
        preflight = tool.dry_run({"prompt": "x"})
        assert preflight["estimated_cost_usd"] is None
        assert preflight["cost_status"] == "quote_required"


def test_selectors_route_to_wavespeed_by_host(monkeypatch, tmp_path):
    from tools.graphics.image_selector import ImageSelector
    from tools.video.video_selector import VideoSelector

    monkeypatch.setenv("WAVESPEED_API_KEY", "test")
    calls = _fake_api(
        monkeypatch,
        results=[_envelope({"id": "pred-1", "status": "completed", "outputs": [IMAGE]})],
    )
    image = ImageSelector().execute(
        {
            "prompt": "A lighthouse",
            "hosting_provider": "wavespeed",
            "aspect_ratio": "3:2",
            "output_path": str(tmp_path / "a.png"),
        }
    )
    assert image.success, image.error
    assert image.data["selected_tool"] == "wavespeed_image"
    assert calls[0][2]["json"] == {"prompt": "A lighthouse", "aspect_ratio": "3:2"}

    calls.clear()
    _fake_api(
        monkeypatch,
        results=[_envelope({"id": "pred-2", "status": "completed", "outputs": [VIDEO]})],
    )
    video = VideoSelector().execute(
        {
            "prompt": "Pan right",
            "hosting_provider": "wavespeed",
            "operation": "image_to_video",
            "image_url": "https://example.com/frame.png",
            "output_path": str(tmp_path / "b.mp4"),
        }
    )
    assert video.success, video.error
    assert video.data["selected_tool"] == "wavespeed_video"
    assert video.data["operation"] == "image_to_video"
