ARCHITECT | FIX_REQUIRED
REF: commit 3108ec20e145233f38e0d4b64fa72672063b9280

REVIEW_TIER
FULL

BLOCK
B1 CROSS_PROJECT_IDENTITY: job `input_hash` is global but excludes project identity. Importing identical source bytes into a second project resolves the first project's SUCCEEDED job and fails `SUCCEEDED -> READY`.
B2 CHECKPOINT_INTEGRITY: `_load_completed` checks path existence only. A zero-byte/tampered metadata artifact is returned as a valid completed checkpoint despite stored size/hash.
B3 SOURCE_DRIFT: source identity is hashed only before probe. Bytes changed during probe can be published with metadata and SHA bound to different source states.
B4 PROJECT_ROOT_BINDING: `project_root` is caller-authored and is not verified against the persisted Project. Generated artifacts can be redirected outside the project's recorded root.

ACCEPT+
- Identical source bytes import independently into two projects without cross-project job/episode substitution.
- Completed reuse requires artifact bytes to match authoritative stored identity and valid expected metadata; invalid evidence is rebuilt safely or fails typed without returning completion.
- Source drift before publication fails closed and publishes no Episode/artifact/SUCCEEDED state.
- Artifact destinations are bound to persisted `Project.root_path`; caller root substitution is ignored or rejected without writes to the substituted root.

ADVERSARIAL_PACK
- `backend/tests/acceptance/test_architect_review_001.py` A1-A4 must PASS at corrected HEAD.

EVIDENCE+
- OLD_HEAD: 3108ec20e145233f38e0d4b64fa72672063b9280
- NEW_HEAD: corrected commit
- A1-A4 exact result and full relevant test count.

GATE
LOCAL_ONLY + NO_LOOP

RETURN
UPDATED or BLOCKED

