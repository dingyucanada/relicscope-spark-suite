## ADDED Requirements

### Requirement: Scout device boundary

The system SHALL expose a versioned Scout API independently of all legacy demo and
administration routes. Every device-scoped operation SHALL require a known, enabled
device identity and credential when authentication is enabled.

#### Scenario: Unknown device submits a job

- **WHEN** an unknown, disabled, or incorrectly authenticated device submits media
- **THEN** the gateway returns `401` and creates no job or media record

#### Scenario: Device attempts to read another device's job

- **WHEN** an authenticated device requests a job owned by another device
- **THEN** the gateway does not disclose the job or result

### Requirement: Immutable idempotent ingestion

The system SHALL accept one to eight bounded image uploads with immutable job metadata,
verify declared versus decoded media types, and bind the request to raw-byte file hashes.
It SHALL preserve original-byte hashes while applying EXIF orientation before server
quality analysis and sanitized model-input generation.

#### Scenario: Network retry repeats the same request

- **WHEN** a device reuses its client job identifier with byte-identical immutable input
- **THEN** the gateway returns the original job without duplicating inference

#### Scenario: Identifier is reused with changed input

- **WHEN** a device reuses its client job identifier with changed metadata or media
- **THEN** the gateway returns `409` and preserves the original job

#### Scenario: Camera stores pixels with EXIF rotation

- **WHEN** an accepted image declares an EXIF orientation
- **THEN** server quality checks and model preparation use the visually normalized
  pixels while the stored source hash remains the SHA-256 of the uploaded bytes

### Requirement: Ingest capacity protection

The system SHALL enforce a configured minimum free-storage reserve and a configured
outstanding-job cap per device without breaking immutable idempotency.

#### Scenario: Device reaches its outstanding-job cap

- **WHEN** a device submits a new client job while its outstanding-job cap is reached
- **THEN** the gateway returns `429`, creates no new job, and still permits a
  byte-identical retry to resolve to an existing job

#### Scenario: Local storage reaches its reserve

- **WHEN** accepting new media would leave less than the configured free-space reserve
- **THEN** the gateway returns `507` and creates no new job or media record

### Requirement: Durable local workflow

The primary Spark SHALL own a restart-safe local job queue and SHALL be capable of
completing the current V2 workflow without a second Spark.

#### Scenario: Gateway restarts during a job

- **WHEN** the gateway restarts with a job in a non-terminal processing state
- **THEN** it recovers the job into the queue without creating a new job identifier

#### Scenario: Late worker tries to overwrite a terminal result

- **WHEN** a completion or failure write targets a job that is no longer `RUNNING`
- **THEN** the transition is rejected and the existing terminal status and result remain
  unchanged

### Requirement: Model availability and same-job retry

The system SHALL avoid consuming queued work while the model health check is offline or
warming, SHALL use `RETRY_WAIT` with bounded exponential delay for transient failures
after a job is claimed, and SHALL retain one job identifier and immutable input across
all automatic attempts.

#### Scenario: Model is starting when a job arrives

- **WHEN** the local model health check is not online
- **THEN** the job remains `QUEUED` and no model attempt is consumed

#### Scenario: Model request fails transiently

- **WHEN** a claimed job's model request fails before the configured attempt limit
- **THEN** that job enters `RETRY_WAIT` and becomes eligible for another attempt after
  its bounded backoff without creating a new job

#### Scenario: Automatic attempts are exhausted

- **WHEN** no model call succeeds within the configured attempt limit
- **THEN** the same job ends as `MODEL_UNAVAILABLE` with no fabricated observations

#### Scenario: Operator restores the model and requests retry

- **WHEN** the owning authenticated device explicitly retries a `MODEL_UNAVAILABLE` job
- **THEN** the gateway clears the unavailable result and requeues that same job and
  immutable input; other terminal states cannot use this transition

#### Scenario: A later attempt succeeds

- **WHEN** one or more completion calls fail and a later completion call succeeds for
  the same job
- **THEN** the structured result includes the append-only proof for every actual call
  in order, without replacing the earlier failures

#### Scenario: Process stops after a successful model outcome is persisted

- **WHEN** the gateway restarts after recording a validated successful output but before
  completing result assembly
- **THEN** it reuses that durable output and completes the same job without issuing a
  duplicate model call

#### Scenario: Process stops while a model outcome is unknown

- **WHEN** an attempt was reserved and issued but no outcome was durably recorded
- **THEN** restart records `UNKNOWN_AFTER_RESTART`, counts that attempt against the same
  bounded budget, and preserves the configured retry delay when another attempt remains

### Requirement: Concurrent content-addressed ingest safety

The gateway SHALL atomically publish content-addressed media without overwrite and SHALL
serialize publication, database reference creation, and rejected-request cleanup.

#### Scenario: Two jobs upload identical image bytes concurrently

- **WHEN** one job is accepted and the other is rejected by an atomic quota or conflict
- **THEN** the accepted job's referenced media remains present and hash-valid

### Requirement: One verifiable analysis path

The V2 Scout API SHALL accept only the `standard` analysis mode. A device SHALL NOT be
able to select a shortcut that bypasses server quality, multi-view model observation,
or provenance capture.

#### Scenario: Device requests an unsupported mode

- **WHEN** a Scout submits an analysis mode other than `standard`
- **THEN** request validation fails and no job or media record is created

### Requirement: Fail-closed visual observation

The system SHALL separate deterministic quality checks from VLM observations and SHALL
not synthesize visual observations when the local model is unavailable.

#### Scenario: All captures fail quality

- **WHEN** no uploaded capture passes the server quality gate
- **THEN** the result is `NEEDS_RECAPTURE`, contains explicit failed checks, and records no
  VLM run

#### Scenario: VLM endpoint is unavailable

- **WHEN** accepted captures exist but no local VLM call succeeds after bounded retries
- **THEN** the result is `MODEL_UNAVAILABLE` and instructs the operator to restore the
  local service

#### Scenario: Stored source media changes after ingestion

- **WHEN** the worker's single pre-inference read does not match the recorded source
  SHA-256, is missing, or cannot be decoded
- **THEN** the job fails with `MEDIA_INTEGRITY_FAILURE` before any model request

#### Scenario: Model response lacks sufficient usable evidence

- **WHEN** a successful model response has no visible observations, reports high OOD
  risk or capture issues, or the job has a server-rejected capture or incomplete
  standard view coverage
- **THEN** the job is `PARTIAL` and the structured result records the limitations and
  recommended next actions

### Requirement: Restricted conclusion scope

Every Scout result SHALL state `authenticity_state=NOT_ASSESSED` and SHALL bind visible
observations to capture identifiers and model-run provenance. A model run SHALL record
the verified configured model, model source, immutable model revision, immutable runtime
image digest, system-prompt hash, exact canonical request-payload hash, provider request
identifier, ordered source and sanitized-input hashes, output hash, and measured latency.

#### Scenario: Standard job succeeds

- **WHEN** the local VLM returns valid visible observations
- **THEN** each observation identifies its source capture and model output hash, while the
  result withholds authenticity, dating, kiln, attribution, valuation, and legal verdicts

#### Scenario: Runtime provenance is mutable or mismatched

- **WHEN** the production gateway is configured with a mutable runtime image, mutable
  model revision, or a model source that differs from the served model identity
- **THEN** startup or deployment preflight fails rather than producing unverifiable
  reports
