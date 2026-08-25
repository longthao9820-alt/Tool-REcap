# D001 — Automated execution and merge authority

Status: ACTIVE  
Human authorization: 2026-08-25 conversation — “Không cần hỏi ý kiến tôi” and prior instruction to automate local work and GitHub pushes.

Decision:

- Architect may autonomously create milestone-sized contracts, delegate implementation to Claude Code, review evidence, merge branches that reach `ARCHITECT | MERGE_READY`, and push `main`.
- No additional Human confirmation is required for normal local implementation, tests, commits, task-branch pushes, or reviewed merges.
- Prefer functional vertical slices/milestones over many small contracts.

Exclusions remain fail-closed:

- no force-push/history rewrite;
- no secret exposure or credential mutation;
- no production deployment or paid provider/API spend;
- no destructive data operation;
- no bypass of security/rights policy;
- stop on product decisions with materially different outcomes.

