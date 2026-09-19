"""Deterministic character resolution from provider-supplied participant labels.

Uncertainty is preserved rather than guessed: a label that carries no identity
(blank, `UNKNOWN`, a bare diarisation id such as `SPEAKER_02`) resolves to the one
UNKNOWN placeholder of the revision instead of inventing a person. Every revision
always owns that placeholder, so a later stage always has a valid target for a
participant it cannot resolve.
"""

from __future__ import annotations

import re
from typing import Callable, Sequence

from ..errors import ValidationError
from .event import Character, CharacterStatus

UNKNOWN_CHARACTER_NAME = "UNKNOWN"
_SPEAKER_LABEL = re.compile(r"^speaker[_\- ]?\d+$")


def canonical_key(name: str) -> str:
    """Whitespace- and case-insensitive identity key for a participant label."""
    return " ".join(str(name).split()).casefold()


def is_unknown_label(name: str) -> bool:
    key = canonical_key(name)
    return not key or key == UNKNOWN_CHARACTER_NAME.casefold() or bool(_SPEAKER_LABEL.match(key))


def resolve_characters(
    analysis_revision_id: str,
    scene_participants: Sequence[tuple[str, Sequence[str]]],
    id_factory: Callable[[], str],
) -> tuple[tuple[Character, ...], tuple[tuple[str, str], ...]]:
    """Return the revision's characters plus its `(scene_id, character_id)` links.

    Scenes are processed in the order given, so ids and links are stable for a
    stable input; the caller keeps full control of id generation.
    """
    unknown = Character(
        id=id_factory(),
        analysis_revision_id=analysis_revision_id,
        canonical_name=UNKNOWN_CHARACTER_NAME,
        status=CharacterStatus.UNKNOWN,
    )
    by_key: dict[str, Character] = {canonical_key(UNKNOWN_CHARACTER_NAME): unknown}
    order: list[Character] = [unknown]
    links: list[tuple[str, str]] = []
    seen_links: set[tuple[str, str]] = set()

    for scene_id, names in scene_participants:
        if not isinstance(scene_id, str) or not scene_id:
            raise ValidationError("character resolution requires a scene id")
        for name in names:
            if is_unknown_label(name):
                character = unknown
            else:
                key = canonical_key(name)
                character = by_key.get(key)
                if character is None:
                    character = Character(
                        id=id_factory(),
                        analysis_revision_id=analysis_revision_id,
                        canonical_name=" ".join(str(name).split()),
                        status=CharacterStatus.RESOLVED,
                    )
                    by_key[key] = character
                    order.append(character)
            link = (scene_id, character.id)
            if link not in seen_links:
                seen_links.add(link)
                links.append(link)
    return tuple(order), tuple(links)
