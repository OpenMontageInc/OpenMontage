"""Tests for PexelsVideo tool (resolution selection and quality fallback)."""

from pathlib import Path
import pytest

from tools.video.pexels_video import PexelsVideo


class _FakeSearchResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeDownloadResponse:
    def __init__(self, content=b"fake-video-bytes"):
        self.content = content

    def raise_for_status(self):
        pass


def test_pexels_video_missing_api_key(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    result = PexelsVideo().execute({"query": "nature"})
    assert not result.success
    assert "PEXELS_API_KEY not set" in result.error


def test_pexels_video_selects_widest_file_when_quality_is_null(monkeypatch, tmp_path):
    """Issue #967: Pexels API returns quality: null on newer videos.
    When preferred_quality is 'hd', it must pick the widest rendition
    instead of the first entry in the unsorted API list (360p).
    """
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    video_files_sample = [
        {"quality": None, "width": 360, "height": 640, "link": "https://pexels.com/360p.mp4"},
        {"quality": None, "width": 540, "height": 960, "link": "https://pexels.com/540p.mp4"},
        {"quality": None, "width": 720, "height": 1280, "link": "https://pexels.com/720p.mp4"},
        {"quality": None, "width": 1080, "height": 1920, "link": "https://pexels.com/1080p.mp4"},
        {"quality": None, "width": 1440, "height": 2560, "link": "https://pexels.com/1440p.mp4"},
        {"quality": None, "width": 2160, "height": 3840, "link": "https://pexels.com/2160p.mp4"},
    ]

    api_payload = {
        "videos": [
            {
                "id": 35594913,
                "duration": 10,
                "user": {"name": "Test Videographer"},
                "video_files": video_files_sample,
            }
        ],
        "total_results": 1,
    }

    downloaded_urls = []

    def fake_get(url, **kwargs):
        if "api.pexels.com" in url:
            return _FakeSearchResponse(api_payload)
        downloaded_urls.append(url)
        return _FakeDownloadResponse()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    out_file = tmp_path / "test_out.mp4"
    result = PexelsVideo().execute({
        "query": "ocean waves",
        "preferred_quality": "hd",
        "output_path": str(out_file),
    })

    assert result.success
    assert result.data["width"] == 2160
    assert result.data["height"] == 3840
    assert downloaded_urls == ["https://pexels.com/2160p.mp4"]
    assert out_file.exists()


def test_pexels_video_selects_sd_when_quality_is_null(monkeypatch, tmp_path):
    """When preferred_quality is 'sd' and quality is null, it should select
    the best file within standard definition (< 720p).
    """
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    video_files_sample = [
        {"quality": None, "width": 360, "height": 640, "link": "https://pexels.com/360p.mp4"},
        {"quality": None, "width": 540, "height": 960, "link": "https://pexels.com/540p.mp4"},
        {"quality": None, "width": 1080, "height": 1920, "link": "https://pexels.com/1080p.mp4"},
    ]

    api_payload = {
        "videos": [
            {
                "id": 12345,
                "duration": 5,
                "user": {"name": "Test User"},
                "video_files": video_files_sample,
            }
        ],
        "total_results": 1,
    }

    downloaded_urls = []

    def fake_get(url, **kwargs):
        if "api.pexels.com" in url:
            return _FakeSearchResponse(api_payload)
        downloaded_urls.append(url)
        return _FakeDownloadResponse()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    out_file = tmp_path / "test_sd.mp4"
    result = PexelsVideo().execute({
        "query": "clouds",
        "preferred_quality": "sd",
        "output_path": str(out_file),
    })

    assert result.success
    assert result.data["width"] == 540
    assert downloaded_urls == ["https://pexels.com/540p.mp4"]


def test_pexels_video_matches_explicit_quality(monkeypatch, tmp_path):
    """When quality is explicitly 'hd' or 'sd', it matches that tag."""
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    video_files_sample = [
        {"quality": "sd", "width": 640, "height": 360, "link": "https://pexels.com/sd.mp4"},
        {"quality": "hd", "width": 1920, "height": 1080, "link": "https://pexels.com/hd.mp4"},
    ]

    api_payload = {
        "videos": [
            {
                "id": 9999,
                "duration": 8,
                "user": {"name": "Test User"},
                "video_files": video_files_sample,
            }
        ],
        "total_results": 1,
    }

    downloaded_urls = []

    def fake_get(url, **kwargs):
        if "api.pexels.com" in url:
            return _FakeSearchResponse(api_payload)
        downloaded_urls.append(url)
        return _FakeDownloadResponse()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    result = PexelsVideo().execute({
        "query": "forest",
        "preferred_quality": "sd",
        "output_path": str(tmp_path / "explicit_sd.mp4"),
    })
    assert result.success
    assert result.data["width"] == 640
    assert downloaded_urls == ["https://pexels.com/sd.mp4"]


def test_pexels_video_no_files_found(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    api_payload = {
        "videos": [
            {
                "id": 8888,
                "duration": 5,
                "video_files": [],
            }
        ],
        "total_results": 1,
    }

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: _FakeSearchResponse(api_payload))

    result = PexelsVideo().execute({"query": "desert"})
    assert not result.success
    assert "No downloadable video file found" in result.error
