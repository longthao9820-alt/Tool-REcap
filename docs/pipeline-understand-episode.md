# Episode understanding pipeline (Milestone 1)

Turns one imported `Episode` into a persisted, schema-valid `RecapPlan`.

## Stage DAG

```text
PREPROCESS ─┬─> TRANSCRIBE ──┬─> BUILD_SCENES -> EXTRACT_EVENTS -> BUILD_STORY_GRAPH -> PLAN_RECAP
            └─> DETECT_SHOTS ┘
```

`EXTRACT_EVENTS` also depends on `TRANSCRIBE`. Declared in
`recap_core/application/understand_episode.py::DEPENDENCIES`; `descendants()` derives
the invalidation set from it.

| Stage | Scope | Produces (artifact kind) | Rows |
|---|---|---|---|
| `PREPROCESS` | analysis revision | `media_proxy`, `stt_audio` | — |
| `TRANSCRIBE` | analysis revision | `transcript` | `transcript_segments` |
| `DETECT_SHOTS` | analysis revision | `shots`, `keyframe`* | `shots` |
| `BUILD_SCENES` | analysis revision | `scenes` | `scenes`, `scene_shots`, `characters`, `scene_characters` |
| `EXTRACT_EVENTS` | analysis revision | `events` | `events`, `evidence_items`, `event_characters` |
| `BUILD_STORY_GRAPH` | analysis revision | `story_graph` | `plots`, `plot_events`, `story_edges` |
| `PLAN_RECAP` | recap revision | `recap_plan` | `recap_plans`, `recap_plan_events` |

## Revisions

- `AnalysisConfig` = media/STT/scene/story versions + STT language. `config_hash =
  H(source_sha256, config)` keys one `analysis_revisions` row. It contains **no**
  recap input, so a profile change cannot create or invalidate an analysis revision.
- `RecapProfile.profile_hash()` keys one `recap_revisions` row under that analysis
  revision. Only `PLAN_RECAP` is scoped to it.
- Changing any analysis version produces a *new* revision; the old one stays intact.

## Checkpoints and resume

A stage is skipped only when its `stage_checkpoints` row satisfies all of:

1. `input_hash` equals the recomputed hash (provenance + scope + every predecessor
   artifact digest);
2. every linked artifact exists on disk with the recorded size **and** SHA-256;
3. every linked stage document still validates against its published schema;
4. `output_json.counts` equals the live row counts for that stage.

Anything else invalidates the stage plus exactly its DAG descendants (deepest first),
which are then rebuilt. Forged completion flags, inflated counts, missing artifact
links and tampered bytes therefore cannot manufacture a downstream pass.

Publication order per stage: run -> write artifacts atomically (`.partial` -> rename)
-> re-verify artifact bytes -> revalidate source SHA-256 -> one transaction writing
artifact rows, domain rows and the checkpoint.

## Providers

Ports live in `recap_core/ports/`: `STTProvider`, `SceneAnalysisProvider`,
`StoryReasoningProvider`, `MediaProcessor`. Every provider result passes through an
`ensure_*` boundary validator before it reaches the domain, whether the adapter
returned a DTO or a raw payload.

- Provider labels can never move authoritative geometry: `parse_scene_label_dto`
  rejects payloads carrying `start_ms`/`end_ms`/`shot_ids`/ids at all.
- Event drafts must sit inside the `ScenePackage` range and reference only transcript
  ids and keyframe timestamps from that package.
- Story reasoning may only reference the event ids the caller supplied.

`recap_core/application/composition.py` is the only production selection point. It
authorizes by registered name *and* by defining module
(`recap_core.infrastructure.providers`), so the deterministic doubles in
`backend/tests/providers.py` are unreachable from production configuration.

`LocalFasterWhisperProvider` requires an existing local model directory and always
loads with `local_files_only=True`; without one, `capability()` reports unavailable
and `transcribe()` raises `ProviderUnavailableError` rather than downloading weights.
No scene-analysis or story-reasoning provider is authorized yet — those need an
external reasoning/vision provider, which is out of scope for this milestone.

## Character resolution

Owned by `BUILD_SCENES`. Participant labels are canonicalised case/whitespace
insensitively; blank, `UNKNOWN` and bare diarisation ids (`SPEAKER_02`) resolve to the
revision's single `UNKNOWN` placeholder. `EXTRACT_EVENTS` maps event participants onto
that existing set and never invents a new identity.

## Published schemas

`schemas/v1/{transcript,shots,scenes,events,story-graph,recap-plan}.json`, enforced by
`recap_core/schemas/validator.py`. The validator implements only the JSON Schema
keyword subset those files use and raises on any keyword it does not know, so an
unsupported keyword is an authoring error, never a silent pass. Documents flatten
`TimeRange` into `start_ms`/`end_ms` and use an integer `schema_version`; that is the
shape the domain `to_dict()` emits.

## Running

```powershell
python -m pytest                     # whole suite
python -m pytest backend/tests/acceptance   # real local FFmpeg/FFprobe gates
```

The acceptance gates have no skip path: without a working local FFmpeg they fail.
