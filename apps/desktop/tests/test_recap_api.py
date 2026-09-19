from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from recap_tool.api_client import OpenAICompatibleClient, parse_json_loose
from recap_tool.recap_api import (
    _episode_id,
    _normalize_episode,
    _parse_subtitles,
    analyze_input,
    recap_prompt_for_content,
)
from recap_tool.settings import AppSettings, SettingsStore
from recap_tool.edit_plan import validate_smooth_edit_plan


def test_api_json_parser_accepts_fence_and_prose() -> None:
    assert parse_json_loose('```json\n{"ok": true}\n```') == {"ok": True}
    assert parse_json_loose('Result: {"ok": true}') == {"ok": True}


def test_api_client_calls_highlight_compatible_route(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def fake_urlopen(api_request, timeout):
        captured["url"] = api_request.full_url
        captured["headers"] = dict(api_request.header_items())
        captured["body"] = api_request.data.decode("utf-8")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("recap_tool.api_client.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient("http://127.0.0.1:20128/v1", "local-key", timeout=12)
    assert client.test("model", "high") == "OK"
    assert captured["url"] == "http://127.0.0.1:20128/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer local-key"
    assert '"reasoning_effort": "high"' in captured["body"]


def test_srt_parser_preserves_source_times(tmp_path) -> None:
    path = tmp_path / "episode.srt"
    path.write_text(
        "1\n00:00:01,250 --> 00:00:03,500\n<i>Hello</i> world\n\n"
        "2\n00:01:04,000 --> 00:01:05,000\nSecond line\n",
        encoding="utf-8",
    )
    assert _parse_subtitles(path) == [
        {"start": 1.25, "end": 3.5, "text": "Hello world"},
        {"start": 64.0, "end": 65.0, "text": "Second line"},
    ]


def test_episode_identity_and_mode_are_bound_to_real_source(tmp_path) -> None:
    video = tmp_path / "Show.S01E02.mkv"
    video.write_bytes(b"video")
    raw = {
        "episode_id": "wrong",
        "source_file": "wrong.mp4",
        "outputs": [
            {"render_id": "full", "type": "FULL_RECAP", "title": "Full Story", "segments": []},
            {"render_id": "story", "type": "MAIN_STORY", "title": "Main Story", "segments": [{}]},
        ],
    }
    assert _episode_id(video, 1) == "S01E02"
    episode = _normalize_episode(raw, "S01E02", video, "MAIN_STORIES", "en-US")
    assert episode["episode_id"] == "S01E02"
    assert episode["source_file"] == video.name
    assert episode["recap_language"] == "en-US"
    assert [item["type"] for item in episode["outputs"]] == ["MAIN_STORY"]


def test_direct_api_settings_round_trip(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    value = AppSettings(
        api_endpoint="http://127.0.0.1:9999/v1",
        api_key="local",
        scanner_model="sub",
        scanner_thinking="max",
        finalizer_model="prime",
        finalizer_thinking="high",
        scanner_parallelism=3,
        api_model="model",
        api_thinking="xhigh",
        api_parallelism=3,
        api_chunk_seconds=420,
        recap_prompt="recap prompt only",
        recap_prompt_version="tvshow-original-commentary-v1",
    )
    store.save(value)
    loaded = store.load()
    assert loaded.api_endpoint == value.api_endpoint
    assert loaded.scanner_model == "sub"
    assert loaded.scanner_thinking == "max"
    assert loaded.finalizer_model == "prime"
    assert loaded.finalizer_thinking == "high"
    assert loaded.scanner_parallelism == 3
    assert loaded.api_model == "model"
    assert loaded.api_parallelism == 3
    assert loaded.api_chunk_seconds == 420
    assert loaded.recap_prompt == "recap prompt only"
    assert loaded.recap_prompt_version == "tvshow-original-commentary-v1"


def test_german_soap_uses_its_dedicated_prompt(tmp_path, monkeypatch) -> None:
    prompt_path = tmp_path / "PROMPT_RECAP_GERMAN_SOAP_HOAN_CHINH.md"
    prompt_path.write_text("GERMAN SOAP PROMPT", encoding="utf-8")
    monkeypatch.setattr("recap_tool.recap_api.application_root", lambda: tmp_path)
    assert recap_prompt_for_content("DE_GERMAN_SOAP") == "GERMAN SOAP PROMPT"


def test_bodycam_uses_its_dedicated_prompt(tmp_path, monkeypatch) -> None:
    prompt_path = tmp_path / "PROMPT_BODYCAM_EVIDENCE_COMMENTARY_HOAN_CHINH.md"
    prompt_path.write_text("BODYCAM PROMPT", encoding="utf-8")
    monkeypatch.setattr("recap_tool.recap_api.application_root", lambda: tmp_path)
    assert recap_prompt_for_content("BODYCAM") == "BODYCAM PROMPT"


def test_direct_api_pipeline_creates_importable_json(tmp_path, monkeypatch) -> None:
    fixture = Path(__file__).resolve().parents[3] / "backend" / "tests" / "fixtures" / "sample_episode.mp4"
    video = tmp_path / "Show.S01E01.mp4"
    shutil.copy2(fixture, video)

    requests = []
    requested_models = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def chat_json(self, *, system, **kwargs):
            requests.append(kwargs.get("user_text", ""))
            requested_models.append((kwargs.get("model"), kwargs.get("thinking")))
            if "evidence scanner" in system:
                return {"range_start_ms": 0, "range_end_ms": 1000, "events": []}
            return {
                "episode_id": "ignored",
                "title": "Episode 1",
                "source_file": "ignored.mp4",
                "outputs": [
                    {
                        "render_id": "story-01",
                        "type": "MAIN_STORY",
                        "title": "Story",
                        "segments": [
                            {
                                "segment_id": "segment-01",
                                "order": 1,
                                "segment_type": "narration",
                                "purpose": "CAUSAL_ANALYSIS",
                                "editorial_value": "Explains why the verified event changes the story rather than merely restating it.",
                                "narration_text": "Power shifts.",
                                "audio_policy": "mute",
                                "preserve_original_audio": False,
                                "subtitle": True,
                                "source_clips": [
                                    {"clip_id": "clip-01", "start_ms": 0, "end_ms": 900, "order": 1}
                                ],
                            }
                        ],
                    }
                ],
            }

    monkeypatch.setattr("recap_tool.recap_api.OpenAICompatibleClient", FakeClient)
    monkeypatch.setattr("recap_tool.recap_api.default_data_directory", lambda: tmp_path / "data")
    monkeypatch.setattr(
        "recap_tool.recap_api.transcribe_video_audio",
        lambda *_args, **_kwargs: [
            {"start": 0.1, "end": 0.8, "text": "Recognized dialogue from the audio."}
        ],
    )
    output = analyze_input(
        video,
        project_name="Direct API",
        language="en-US",
        mode="MAIN_STORIES",
        content_type="US_TV_SHOW",
        settings=AppSettings(api_chunk_seconds=300),
        prompt="Use verified evidence.",
        log=lambda _message: None,
        progress=lambda _done, _total, _message: None,
        cancel_event=threading.Event(),
    )
    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["episodes"][0]["source_file"] == "Show.S01E01.mp4"
    normalized_segment = payload["episodes"][0]["outputs"][0]["segments"][0]
    assert normalized_segment["estimated_voice_duration_ms"] > 0
    assert normalized_segment["source_visual_duration_ms"] == 900
    assert normalized_segment["recommended_visual_speed"] > 0
    assert any("Recognized dialogue from the audio." in item for item in requests)
    assert any('"transcript_source":"local_faster_whisper"' in item for item in requests)
    assert requested_models[0] == ("sub", "max")
    assert requested_models[-1] == ("prime", "high")


def test_smooth_edit_gate_rejects_micro_clip_chain_and_aggressive_speed() -> None:
    clips = [
        {"clip_id": f"clip-{index}", "start_ms": index * 1000, "end_ms": index * 1000 + 900, "order": index + 1}
        for index in range(6)
    ]
    episode = {
        "outputs": [
            {
                "render_id": "story",
                "segments": [
                    {
                        "segment_id": "rapid",
                        "segment_type": "narration",
                        "purpose": "CAUSAL_ANALYSIS",
                        "recommended_visual_speed": 1.35,
                        "source_clips": clips,
                    }
                ],
            }
        ]
    }
    try:
        validate_smooth_edit_plan(episode, content_type="US_TV_SHOW")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Micro-clip chain should have been rejected")
    assert "tối đa 4" in message
    assert "dưới 2 giây" in message
    assert "0.90–1.10" in message


def test_smooth_edit_gate_accepts_two_long_natural_speed_clips() -> None:
    episode = {
        "outputs": [
            {
                "render_id": "story",
                "segments": [
                    {
                        "segment_id": "smooth",
                        "segment_type": "narration",
                        "purpose": "CONSEQUENCE",
                        "recommended_visual_speed": 1.02,
                        "source_clips": [
                            {"clip_id": "one", "start_ms": 0, "end_ms": 3500, "order": 1},
                            {"clip_id": "two", "start_ms": 6000, "end_ms": 10000, "order": 2},
                        ],
                    }
                ],
            }
        ]
    }
    validate_smooth_edit_plan(episode, content_type="US_TV_SHOW")
