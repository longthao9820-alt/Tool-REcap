"""Production provider authorization (V3) and the offline Faster-Whisper adapter."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from recap_core.application.composition import (
    AUTHORIZED_PROVIDER_PACKAGE,
    SCENE_ANALYSIS_PROVIDERS,
    STORY_REASONING_PROVIDERS,
    STT_PROVIDERS,
    assert_production_provider,
    build_scene_analysis_provider,
    build_stt_provider,
    build_story_reasoning_provider,
)
from recap_core.domain.errors import ProviderNotAuthorizedError, ProviderUnavailableError
from recap_core.infrastructure.providers.faster_whisper_stt import (
    LocalFasterWhisperProvider,
    missing_model_files,
)
from tests.providers import (
    DeterministicSceneAnalysisProvider,
    DeterministicSTTProvider,
    DeterministicStoryReasoningProvider,
)

CORE_ROOT = Path(__file__).resolve().parents[2] / "recap_core"


# -- V3: test adapters are unreachable from production composition ------------


@pytest.mark.parametrize(
    "provider",
    [
        DeterministicSTTProvider(),
        DeterministicSceneAnalysisProvider(),
        DeterministicStoryReasoningProvider(),
    ],
)
def test_v3_deterministic_test_adapters_are_not_production_providers(provider):
    with pytest.raises(ProviderNotAuthorizedError):
        assert_production_provider(provider)


@pytest.mark.parametrize(
    "builder", [build_stt_provider, build_scene_analysis_provider, build_story_reasoning_provider]
)
def test_v3_test_adapter_names_cannot_be_selected_by_configuration(builder):
    for name in (
        DeterministicSTTProvider.name,
        DeterministicSceneAnalysisProvider.name,
        DeterministicStoryReasoningProvider.name,
        "",
        "anything-else",
    ):
        with pytest.raises(ProviderNotAuthorizedError):
            builder(name)


def test_only_local_faster_whisper_is_an_authorized_production_stt_provider():
    assert set(STT_PROVIDERS) == {LocalFasterWhisperProvider.name}
    assert SCENE_ANALYSIS_PROVIDERS == {}
    assert STORY_REASONING_PROVIDERS == {}
    provider = build_stt_provider(LocalFasterWhisperProvider.name)
    assert isinstance(provider, LocalFasterWhisperProvider)
    assert_production_provider(provider)


def test_production_code_never_imports_the_test_provider_module():
    checked = 0
    for path in CORE_ROOT.rglob("*.py"):
        checked += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith("tests"), f"{path.name} imports {name}"
    assert checked > 0


def test_authorized_package_is_the_infrastructure_provider_package():
    assert AUTHORIZED_PROVIDER_PACKAGE == "recap_core.infrastructure.providers"


# -- Faster-Whisper adapter: offline, fail-closed ----------------------------


def test_capability_is_unavailable_without_a_configured_local_model():
    capability = LocalFasterWhisperProvider().capability()
    assert capability.available is False
    assert "download" in capability.detail


def test_capability_is_unavailable_for_a_missing_or_incomplete_model_dir(tmp_path):
    absent = LocalFasterWhisperProvider(tmp_path / "no-such-model").capability()
    assert absent.available is False

    incomplete = tmp_path / "model"
    incomplete.mkdir()
    (incomplete / "config.json").write_text("{}", encoding="utf-8")
    capability = LocalFasterWhisperProvider(incomplete).capability()
    assert capability.available is False
    assert "model.bin" in capability.detail
    assert missing_model_files(incomplete) == ("model.bin",)


def test_transcribe_fails_closed_instead_of_downloading_a_model(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    with pytest.raises(ProviderUnavailableError):
        LocalFasterWhisperProvider().transcribe(audio, "vi")


def test_model_loader_always_requests_local_files_only(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    kwargs = LocalFasterWhisperProvider(model_dir).model_kwargs()
    assert kwargs["local_files_only"] is True
    assert kwargs["model_size_or_path"] == str(model_dir.resolve())
    with pytest.raises(ProviderUnavailableError):
        LocalFasterWhisperProvider().model_kwargs()


def test_provider_identity_is_stable_for_cache_keys(tmp_path):
    provider = LocalFasterWhisperProvider(tmp_path / "whisper-small")
    assert provider.name == "faster-whisper"
    assert provider.version == "faster-whisper-local-1"
    assert provider.model == "whisper-small"


def test_segment_conversion_rejects_non_finite_provider_timestamps():
    from recap_core.domain.errors import ProviderOutputInvalidError

    class Segment:
        start = float("inf")
        end = 1.0
        text = "x"
        words = ()
        avg_logprob = -0.1

    with pytest.raises(ProviderOutputInvalidError):
        LocalFasterWhisperProvider._segment(Segment())
