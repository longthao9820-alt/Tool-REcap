ARCHITECT | READY
REF: SELF

GOAL
Establish a runnable backend foundation that imports one local episode as an immutable source identity, probes normalized media metadata, and persists a resumable completed checkpoint.

SCOPE
IN: Python backend foundation, versioned database migration, domain models/ports, local filesystem project layout, durable job/checkpoint state, FFprobe adapter, focused tests, minimal developer documentation.
R: `BLUEPRINT.md` sections A, E, F, G, I, J, Y, Z.
W: repository files under this workspace only.
OUT: React/Tauri UI, proxy/transcription/scenes/story/AI/TTS/sync/timeline/render, external providers, credentials, remote/GitHub actions, deployment.

INVARIANTS
- Imported source bytes are never modified.
- Episode identity is bound to source SHA-256 and normalized probe metadata.
- Completed checkpoint publication is transactional: no completed state can reference missing/invalid output.
- Time/duration/count values are validated and finite; malformed probe output fails closed.
- Jobs are idempotent by stage/scope/input identity and interrupted work can resume without duplicating a valid completed stage.
- Domain code does not depend on FastAPI, SQLite, subprocess, or FFmpeg implementation modules.

ACCEPT
- A clean local setup can run the focused and full relevant backend test suite. [UNIT]
- Importing a valid media fixture creates the specified project directories, one source-bound Episode record, normalized media metadata, and one successful checkpoint. [UNIT]
- Repeating the same import is idempotent and does not duplicate Episode/job/checkpoint identity. [UNIT]
- Simulated interruption before publication does not expose a false completed checkpoint; a later run can resume and complete. [UNIT]
- Missing source, changed source bytes, invalid FFprobe JSON, non-finite/invalid duration, and unavailable FFprobe produce typed failures without completed state. [UNIT]
- Migration starts from an empty database and preserves foreign-key integrity. [UNIT]

NEGATIVE
- N1: source fixture hash before/after import must be identical.
- N2: valid-looking caller metadata cannot replace actual source hash/probe evidence.
- N3: a partial artifact/checkpoint cannot be read as complete after simulated failure.
- N4: paths outside the selected project root cannot be used as generated artifact destinations.

EVIDENCE
- Current commit identity.
- Exact focused/full test results with pass counts.
- File list/diff sufficient to review dependency boundaries.

BUDGET
Inspect once; implement the smallest sufficient vertical foundation; use narrow tests while debugging; run full relevant backend validation once.

FORBIDDEN
- Source mutation, fake/hardcoded PASS, skipped required negative tests, secrets, network/provider calls, broad future-module scaffolding without current acceptance value.

GATE
LOCAL_ONLY + NO_LOOP

STOP
- Acceptance requires architecture/scope expansion.
- Required local dependency is unavailable after one materially different bounded method.
- Existing workspace changes conflict with the contract.

RETURN
DONE or BLOCKED

