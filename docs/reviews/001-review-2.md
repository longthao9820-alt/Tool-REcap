ARCHITECT | FIX_REQUIRED
REF: commit 9526ef37ffea7f36c9063e8aef1c4be4d4fa7a77

BLOCK
B3 remains incomplete. Source is re-hashed after probe but before artifact construction and the existing `before_publish` boundary. Bytes changed after that re-hash are still published under the earlier SHA with job `SUCCEEDED`.

ACCEPT+
- Revalidate source identity at the final publication boundary. Any drift before DB publication fails closed with no Episode/artifact/SUCCEEDED state.

ADVERSARIAL_PACK+
- `backend/tests/acceptance/test_architect_review_001.py::test_a3b_source_drift_after_probe_before_publish_fails_closed`

EVIDENCE+
- OLD_HEAD: 9526ef37ffea7f36c9063e8aef1c4be4d4fa7a77
- NEW_HEAD: corrected commit
- A3B exact result; A1-A4 and full relevant suite remain PASS.

GATE
LOCAL_ONLY + NO_LOOP

RETURN
UPDATED or BLOCKED
