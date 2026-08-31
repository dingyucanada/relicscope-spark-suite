# Change: Reframe V2 as Scout + DGX Spark local AI infrastructure

## Why

V1 demonstrated many future RelicScope concepts in one browser experience. That shape
obscures the customer's immediate need: turn two purchased DGX Spark systems into a
usable, supportable local AI appliance for a handheld Android Scout.

V2 therefore treats data-corpus construction, large knowledge systems, scientific
instruments, autonomous agents, and institution-wide user identity/RBAC as optional extensions. Device authentication remains mandatory. The
required product is a secure capture-to-local-inference loop that runs on one Spark and
can use a second Spark without making it a dependency.

## What Changes

- Add a native Scout device contract for authenticated image upload, durable jobs,
  status polling, and structured results.
- Add a separate V2 gateway application so legacy demo APIs are not exposed to Scout
  devices on the LAN.
- Make one DGX Spark a complete deployable unit: gateway, durable local state, media,
  local VLM, and result assembly.
- Define the second Spark as an optional independent model, evaluation, fine-tuning, or
  warm-standby node before considering distributed tensor parallelism.
- Keep the workflow deterministic. Agent, RAG, reference recognition, and scientific
  sensor adapters are capability interfaces rather than critical-path dependencies.
- Add HTTPS ingress, per-device credentials, immutable input hashes, content-addressed
  local media, server-side quality verification, and a restart-safe job queue.
- Preserve raw-byte hashes while applying EXIF orientation to quality/model pixels;
  verify stored media again immediately before inference.
- Keep cold-start work queued, add same-job bounded model retries and an explicit
  operator retry after `MODEL_UNAVAILABLE`, and prevent late workers from overwriting a
  terminal result.
- Record immutable model/runtime identity and exact system-prompt, request-payload,
  source-input, model-input, and output hashes in each model run.
- Protect the appliance with a free-space reserve and per-device outstanding-job cap;
  downgrade empty, high-OOD, incomplete-view, or capture-issue model responses to
  `PARTIAL` rather than presenting them as complete.
- Add an Android reference client and hardware acceptance workflow.

## Impact

- New API namespace: `/api/v2/scout`.
- New runtime entry point: `app.scout_main:app`.
- New deployment: `compose.v2.yml` with private model service and HTTPS ingress.
- Existing V1 routes, UI, reference library, and scientific demo remain available on
  `main` and as legacy components, but are not the V2 mobile-facing application.
- No claim of DGX Spark throughput, model quality, dual-node speedup, fine-tune quality,
  or Android hardware support is made until target-machine acceptance evidence exists.
