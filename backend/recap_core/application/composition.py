"""Production composition root for analysis providers.

Only adapters that live in `recap_core.infrastructure.providers` may be selected
here, and only under a name registered below. Deterministic test doubles live in
the test tree, so they have no registered name and no authorized module prefix:
production configuration cannot reach them, by construction rather than by
convention.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..domain.errors import ProviderNotAuthorizedError
from ..infrastructure.providers.faster_whisper_stt import LocalFasterWhisperProvider

#: Adapters are only production-authorized from this package.
AUTHORIZED_PROVIDER_PACKAGE = "recap_core.infrastructure.providers"

STT_PROVIDERS: dict[str, Callable[..., Any]] = {
    LocalFasterWhisperProvider.name: LocalFasterWhisperProvider,
}

#: Scene analysis and story reasoning need an external reasoning/vision provider.
#: This milestone ships the ports and validation only, so no name is authorized
#: yet and selection fails closed instead of silently degrading to a stub.
SCENE_ANALYSIS_PROVIDERS: dict[str, Callable[..., Any]] = {}
STORY_REASONING_PROVIDERS: dict[str, Callable[..., Any]] = {}


def assert_production_provider(provider: object) -> None:
    """Fail closed unless `provider` is defined in the authorized adapter package."""
    module = type(provider).__module__
    if module != AUTHORIZED_PROVIDER_PACKAGE and not module.startswith(
        AUTHORIZED_PROVIDER_PACKAGE + "."
    ):
        raise ProviderNotAuthorizedError(
            "{0}.{1} is not a production provider: it lives in {2!r}, outside {3!r}".format(
                module, type(provider).__name__, module, AUTHORIZED_PROVIDER_PACKAGE
            )
        )


def _build(registry: dict[str, Callable[..., Any]], kind: str, name: str, **kwargs: Any):
    factory = registry.get(name)
    if factory is None:
        raise ProviderNotAuthorizedError(
            "{0} provider {1!r} is not authorized for production; authorized: {2}".format(
                kind, name, sorted(registry) or "none"
            )
        )
    provider = factory(**kwargs)
    assert_production_provider(provider)
    return provider


def build_stt_provider(name: str, *, model_dir: Path | None = None, **kwargs: Any):
    return _build(STT_PROVIDERS, "stt", name, model_dir=model_dir, **kwargs)


def build_scene_analysis_provider(name: str, **kwargs: Any):
    return _build(SCENE_ANALYSIS_PROVIDERS, "scene-analysis", name, **kwargs)


def build_story_reasoning_provider(name: str, **kwargs: Any):
    return _build(STORY_REASONING_PROVIDERS, "story-reasoning", name, **kwargs)
