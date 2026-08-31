# Tasks

## 1. Scope and architecture

- [x] Freeze Scout, single-Spark, second-Spark, model, Agent, RAG, and database boundaries.
- [x] Record official DGX Spark capability and limitation sources.
- [x] Define target-hardware acceptance gates instead of theoretical performance claims.

## 2. Gateway and workflow

- [x] Add separate V2 application and authenticated API namespace.
- [x] Add device enrollment, revocation, and salted token storage.
- [x] Add multipart multi-image ingestion with MIME, size, decode, and duplicate gates.
- [x] Preserve raw-byte hashes, normalize EXIF orientation for analysis, and generate
      EXIF-free bounded model inputs.
- [x] Reverify source-media hashes immediately before inference and fail before a model
      call on integrity mismatch.
- [x] Add persistent jobs, idempotency, restart recovery, ordered captures, stages,
      events, results, and terminal-transition guards.
- [x] Keep model cold starts queued; add `RETRY_WAIT`, bounded same-job model retries,
      terminal `MODEL_UNAVAILABLE`, and explicit same-job operator retry.
- [x] Add server quality verification and `PARTIAL` handling for empty observations,
      high OOD risk, model capture issues, rejected captures, or incomplete view sets.
- [x] Add immutable model/runtime identity, exact request/provenance hashes, and the
      scientific conclusion boundary.
- [x] Add per-device outstanding-job and appliance free-storage safeguards.
- [x] Preserve append-only proof for every actual model call across automatic and
      operator retries, and expose only the standard analysis path.
- [x] Make model attempts crash-safe and bounded, and protect identical content-addressed
      uploads from concurrent rejected-request cleanup.

## 3. Runtime

- [x] Add ARM64 V2 Compose with private vLLM and HTTPS ingress.
- [x] Add preflight, device provisioning, local CA export, and CLI smoke tools.
- [x] Add immutable preparation manifests, a separate second-Spark lab profile, and
      validated primary-state backup/restore tooling.
- [ ] Run the full runtime on the customer's first DGX Spark and freeze measurements.
- [ ] Configure and validate the second Spark in independent-worker/lab role.
- [ ] Evaluate ConnectX-7 distributed inference only if a chosen model requires it.

## 4. Scout client

- [x] Implement and statically verify the Android CameraX reference client, persistent
      Room/WorkManager upload queue, Keystore credentials, and HTTPS-only transport.
- [ ] Install on the customer's Scout hardware and validate cameras, storage, CA trust,
      background retry, battery, and five-view UX.

## 5. Verification and release

- [x] Add API/auth/idempotency/quality/model-boundary tests.
- [x] Run the complete repository test and deployment policy suite.
- [x] Run Android unit tests and lint in an Android SDK environment.
- [ ] Run ten consecutive real jobs and concurrent-client tests on Spark.
- [ ] Commit and publish the V2 branch after local verification.
