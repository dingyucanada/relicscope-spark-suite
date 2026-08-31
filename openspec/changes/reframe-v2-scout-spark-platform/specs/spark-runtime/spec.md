## ADDED Requirements

### Requirement: Single-Spark complete runtime

The V2 deployment SHALL provide a primary ARM64 DGX Spark runtime containing the Scout
gateway, durable local state, local media, and a private local model endpoint. Production
configuration SHALL pin both model revision and runtime image by immutable identity.

#### Scenario: Second Spark is absent

- **WHEN** the secondary Spark is powered off or unconfigured
- **THEN** a standard Scout job can still reach a valid terminal state on the primary

#### Scenario: Model container is still warming

- **WHEN** the gateway is ready but the private model endpoint has not become healthy
- **THEN** the appliance reports degraded operational status and leaves new jobs queued
  until the model becomes healthy

### Requirement: Local storage guardrail

The primary runtime SHALL expose local storage readiness and SHALL reserve configured
free capacity for safe appliance operation.

#### Scenario: Data volume falls below reserve

- **WHEN** free data-volume capacity is below the configured reserve
- **THEN** operational health is degraded and new media ingestion is rejected without
  modifying existing jobs or media

### Requirement: Private model service

The model endpoint SHALL not be published to the Scout LAN. The HTTPS gateway SHALL be
the only mobile-facing service.

#### Scenario: Scout calls the model port

- **WHEN** a Scout attempts to reach the vLLM or NIM service directly from the LAN
- **THEN** no host-published model port is available

#### Scenario: Model container starts

- **WHEN** the production model service is created
- **THEN** it runs as the configured non-root operator, does not share host IPC, and
  receives the GPU through the container runtime without publishing its port

### Requirement: Reproducible disaster recovery

The primary runtime SHALL provide an operator-invoked consistent backup and validated
restore path for local job/media state and the local TLS identity. Secrets, model
weights, runtime images, and mutable environment files SHALL remain outside the archive.

#### Scenario: Operator restores an accepted archive

- **WHEN** archive and per-file hashes, safe members, source version, model identity,
  and destination capacity pass validation
- **THEN** the current data directories are retained as rollback directories and the
  restored service remains stopped until preflight and smoke acceptance run

### Requirement: Evidence-based hardware acceptance

The deployment SHALL distinguish official theoretical hardware capacity from measured
RelicScope performance on the target machines.

#### Scenario: Installation completes

- **WHEN** containers and a model start successfully
- **THEN** documentation may report installation success but not throughput, accuracy,
  fine-tune quality, dual-node speedup, or operational capacity until benchmark evidence
  is recorded

### Requirement: Optional second node

The second Spark SHALL initially serve as an independently deployable model, evaluation,
fine-tuning, batch, or standby node. Distributed tensor parallelism SHALL require an
explicit model-specific acceptance.

#### Scenario: Larger model is proposed

- **WHEN** a chosen model cannot meet single-node memory or latency gates
- **THEN** the team evaluates an official or reproduced ConnectX-7 distributed recipe and
  records measured behavior before promoting it
