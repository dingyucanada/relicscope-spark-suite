# Design: Scout + Spark V2

## 1. Decision

The product boundary is:

```text
Android Scout          Primary DGX Spark              Optional second Spark
capture + local QC  -> secure gateway + durable job -> alternate/heavy model
offline retry          local VLM + local result        fine-tune/evaluation/standby
```

The primary Spark MUST complete a standard job without the second Spark. The second
Spark MUST NOT own the only copy of device, job, media, or result state.

## 2. Why the workflow is deterministic

Capture validation, quality gates, task states, model selection, boundary enforcement,
and result assembly are application code. A model observes images inside this policy.
An autonomous agent is not needed to route the initial workflow and would make latency,
failure recovery, and output boundaries harder to verify.

Later Agent tools may request a recapture, search an approved knowledge pack, or call a
sensor adapter. Those tools remain allow-listed and their outputs are evidence inputs,
not independent verdicts.

## 3. Data path

1. Scout assigns stable client job/capture identifiers and declared view codes.
2. Android runs a fast local quality check and keeps the batch in a persistent queue.
3. Scout sends multipart image bytes and immutable JSON metadata over HTTPS.
4. The gateway authenticates the device and applies the body, MIME, decode, dimension,
   duplicate-media, per-device outstanding-job, storage-reserve, and idempotency gates.
5. Original bytes are stored locally under their raw-byte SHA-256. EXIF orientation is
   applied before server quality analysis and model preparation while the original-byte
   hash remains unchanged. Model input is a bounded JPEG re-encode with EXIF removed.
6. Immediately before inference, the worker reads each source once, verifies its bytes
   against the recorded SHA-256, and derives the model pixels from those verified bytes.
   Missing, changed, or undecodable source media fails with
   `MEDIA_INTEGRITY_FAILURE` before a model call.
7. A restart-safe SQLite queue moves the job through quality, VLM observation, and
   deterministic result assembly while preserving the client's capture order.
8. Scout polls the job and result endpoints. A network retry with the same immutable
   payload returns the same job.

## 4. Job model

States are `QUEUED`, `RUNNING`, `RETRY_WAIT`, `SUCCEEDED`, `PARTIAL`,
`NEEDS_RECAPTURE`, `MODEL_UNAVAILABLE`, `FAILED`, and `CANCELLED`. Stages are kept
separate:

```text
INGEST_VALIDATION -> QUALITY_CHECK -> MULTIMODAL_OBSERVATION -> RESULT_ASSEMBLY
```

The worker checks local model readiness before it claims work. A model that is still
loading or offline therefore leaves new work in `QUEUED`. If a request fails after a
job was claimed, the same job enters `RETRY_WAIT` and uses bounded exponential retries.
Exhausting the configured attempt limit produces `MODEL_UNAVAILABLE`; after the local
model is repaired, an authenticated operator/device action may requeue that same job
and immutable input. The server never fabricates observations.

Completion and failure writes are conditional on `RUNNING`, so a late worker cannot
overwrite a terminal result. The explicit `MODEL_UNAVAILABLE` retry transition is the
only implemented path that reopens a terminal job, and it clears the prior result before
reprocessing.

Every external completion call receives an atomically reserved, monotonically increasing
attempt number before the request is issued. Its validated output and proof are appended
durably before result assembly. A failed outcome and its `RETRY_WAIT` transition are one
transaction. On restart, a recorded success is reused without another model call; a
started call with no recorded outcome is marked `UNKNOWN_AFTER_RESTART` and still consumes
its bounded attempt. A later success therefore cannot erase an earlier error, unknown
outcome, runtime identity, hash, or latency record.

Content-addressed media is published without overwriting an existing object. Publication,
database reference creation, and rejected-request cleanup share an appliance-local
exclusive lock so concurrent ingests of identical bytes cannot let one rejected request
remove media referenced by another accepted job.

A successful model call still yields `PARTIAL` when observations are empty, the model
reports high out-of-distribution risk, the model reports capture issues, a capture fails
server quality, or standard view coverage is incomplete. These conditions remain
visible in the structured result and next actions.

## 5. Model strategy

- Gateway integration uses an OpenAI-compatible private endpoint, so a Spark-compatible
  NVIDIA NIM or vLLM container can be selected after target validation.
- A Qwen vision-language model remains the Chinese baseline candidate.
- Nemotron 3 Nano Omni remains a video/multimodal shadow candidate; its documented
  English language boundary prevents automatic promotion to the Chinese reporting path.
- Prompting and structured output come first. LoRA/PEFT starts only after a frozen task
  evaluation demonstrates a measurable gap and approved training examples exist.
- Each deployed model is bound to source, immutable revision, container, system-prompt
  hash, exact request-payload hash, provider request ID, input hash, output hash, and
  measured latency.
- Production startup rejects a mutable or mismatched model identity. The result records
  the exact configured model/source/revision and immutable runtime image digest, plus
  the system-prompt hash, canonical request-payload hash, provider request ID, ordered
  source hashes, sanitized model-input hashes, output hash, and measured latency.
- The mobile API exposes only the `standard` analysis mode in V2. It has no client-
  selectable shortcut that bypasses multi-view quality, provenance, or model checks.

## 6. Two-Spark policy

Initial roles:

- Primary: production Scout gateway, state, media, standard VLM, and results.
- Secondary: alternate/heavy model, A/B evaluation, PEFT/SFT, batch work, or manually
  promoted standby.

ConnectX-7 can later support distributed runtimes, but two devices are not a transparent
256 GB computer. Tensor parallelism is introduced only for a model that fails the
single-node memory/performance acceptance and has a verified multi-node recipe.

## 7. Security boundary

- Only the HTTPS gateway is reachable from the Scout network.
- vLLM/NIM ports stay on an internal container or Spark-to-Spark network.
- Each Scout receives a revocable token stored with a salted scrypt hash on Spark.
- Production Android trusts an institution/public CA. The debug build may trust a
  user-installed Caddy local CA for a controlled demonstration.
- No cloud endpoint is part of the runtime path; model and container download occurs in
  a separately approved preparation window.
- Raw media, credentials, databases, model caches, and runtime results are excluded from
  Git.
- The gateway reserves a configured amount of free local storage and caps outstanding
  jobs per device. A byte-identical idempotent retry remains readable even when the
  device is at its outstanding-job cap; new work is rejected with an explicit capacity
  response.

## 8. Explicit non-goals for this change

- Building or licensing an art-object corpus.
- Authenticity, dating, kiln, attribution, valuation, or legal decisions.
- Full autonomous Agent operation.
- Production multi-tenant identity, institutional RBAC, MDM, or automatic control-plane
  failover.
- Live scientific-instrument control and calibrated metrology.
- Claiming dual-node acceleration without measured target evidence.
