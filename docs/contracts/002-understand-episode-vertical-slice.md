ARCHITECT | READY
REF: docs/reviews/001-final.md

GOAL
Produce a source-bound, schema-valid basic RecapPlan from one imported episode through a durable functional vertical slice: media preprocessing -> transcript -> shots/scenes -> events/plots/StoryGraph -> story budget/plan.

SCOPE
IN:
- Forward migration(s) for analysis revisions, artifact dependencies, transcript segments, shots, scenes, characters, events/evidence, plots/edges, recap revisions/plans, and durable stage checkpoints.
- Dependency-aware workflow orchestration from the existing imported Episode.
- Real local FFmpeg/FFprobe adapters for 720p proxy, mono STT audio, shot/keyframe extraction, bounded commands, atomic artifacts, and source identity binding.
- `STTProvider`, scene-analysis, and story-reasoning ports with validated provider-neutral DTOs; a production-shaped Faster-Whisper adapter that does not download/run a model during this LOCAL_ONLY contract.
- Deterministic test adapters only for UNIT execution of AI/STT boundaries; they must not be reachable as production defaults or satisfy claims of real model quality.
- Shot-to-scene grouping, ScenePackage construction, event/evidence extraction contract, variable plot discovery contract, typed StoryGraph edges, Full-plot StoryBudget, and basic RecapPlan.
- Versioned schemas/provenance/cache keys, resume/invalidation logic, focused/integration/acceptance tests, concise developer documentation.

R:
- `BLUEPRINT.md` sections C,F-H,I-M,N,W,Y,Z.
- Existing Contract 001 implementation/invariants.

W:
- Repository only; dedicated task branch from current `main`.

OUT:
- React/Tauri/FastAPI UI boundary, external/paid provider calls, provider credentials, full semantic-quality claims, narration/TTS/grounding/clip selection/sync/timeline/render/QC, season batching, deployment.

INVARIANTS
- Analyze once/generate many: changing recap profile/plan inputs must not recreate valid media/transcript/scene/StoryGraph outputs.
- Every stage output is bound to episode source SHA, producer/config/schema/prompt/provider version as causally relevant; cross-project/revision/artifact substitution fails closed.
- Completed checkpoints are readable only when DB identity and on-disk artifact hash/size/schema agree.
- Stage publication is atomic; no downstream stage reads partial/invalid output.
- Resume starts at the first invalid/incomplete DAG node and never reruns an accepted causally valid predecessor.
- Source bytes remain read-only and are revalidated before each stage publication that derives source evidence.
- All time ranges are finite integer milliseconds within episode duration; scenes contain ordered shots; events stay inside scenes and carry non-empty source evidence before StoryGraph acceptance.
- StoryGraph edge endpoints must exist in the same analysis revision; unsupported/cross-revision references fail closed.
- MUST_HAVE setup/payoff/causal bridges cannot be silently dropped from the basic RecapPlan.
- Domain modules remain independent from SQLite/subprocess/FFmpeg/provider SDKs.

ACCEPT
- The committed media fixture runs end-to-end from persisted Episode to a persisted, schema-valid RecapPlan using real local media adapters and deterministic test providers; every artifact is hash/size/provenance bound. [UNIT+ARTIFACT]
- Proxy is decodable 720p-or-lower preserving aspect ratio; STT audio is decodable mono 16 kHz; at least one valid shot/keyframe is source-timestamped. [ARTIFACT]
- Transcript, ScenePackages, events/evidence, variable plots, typed StoryGraph, StoryBudget, and ordered RecapPlan persist under one immutable analysis revision and one recap revision. [UNIT]
- A simulated interruption at each major boundary resumes from the first incomplete stage; completed predecessor call counts/artifact identities remain unchanged. [UNIT]
- Re-running identical inputs is idempotent. Changing only recap profile creates a new recap revision/plan and reuses the exact analysis revision/artifacts. [UNIT]
- Invalid/non-finite/out-of-range timestamps, malformed provider output, missing evidence, unknown edge endpoints, cross-revision IDs, corrupt artifacts, source drift, unavailable FFmpeg/Faster-Whisper capability, and unauthorized production test adapters fail typed without downstream completion. [UNIT]
- Empty-database migration and upgrade from migration 0001 both preserve foreign keys and existing Contract 001 data. [UNIT]
- Existing Contract 001 adversarial pack and all prior tests remain PASS. [UNIT]

NEGATIVE
- V1: a caller-forged `analysis_complete`/derived count cannot manufacture downstream PASS.
- V2: StoryGraph cannot reference an event/evidence item from another analysis revision.
- V3: a deterministic test provider cannot be selected by production composition/configuration.
- V4: recap-profile-only change cannot invalidate or rewrite analysis artifacts.
- V5: a corrupt predecessor artifact cannot be skipped via stale DB `SUCCEEDED` state.
- V6: stage retry/resume cannot publish duplicate rows or overwrite a causally valid immutable revision.

EVIDENCE
- Base/head commit identities and exact changed-file boundary.
- Exact focused, new vertical-slice, Contract 001 adversarial, and full test counts at final HEAD.
- Raw ffprobe evidence for generated proxy/audio/keyframe artifacts plus recorded SHA-256/size/cache/provenance identities.
- Resume/invalidation call-count and artifact-identity evidence for the acceptance fixture.

BUDGET
One substantial implementation pass for the complete vertical slice; narrow tests during debugging; one full relevant validation after focused acceptance passes. One materially different bounded local method may replace an unavailable tool method without changing architecture or scope.

FORBIDDEN
- Network/provider calls, model download, paid API use, mock/synthetic evidence presented as real model/media quality, production selection of test adapters, hardcoded PASS/StoryGraph/RecapPlan, skipped negative gates, source mutation, broad UI/later-pipeline scaffolding, microservices/distributed queue.

GATE
LOCAL_ONLY + NO_LOOP

STOP
- A required architecture/product choice materially changes GOAL/ACCEPT.
- Real local media capability remains unavailable after one bounded alternative method.
- Legitimate completion requires network, credential, paid provider, destructive migration, or scope expansion.
- Security/rights boundary is implicated.

RETURN
DONE or BLOCKED

