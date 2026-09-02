# RelicScope repository guide for Codex

## Objective

Help an authorized operator reproduce, test, or maintain the RelicScope Spark Suite
without weakening its scientific, privacy, or deployment boundaries.

## Current V2 baseline

On `main`, the recommended pilot is Android Scout plus one DGX
Spark running `app.scout_main:app`, HTTPS ingress, a durable job queue, and the
Qwen3.6-35B-A3B NVIDIA NIM for VLM. The NIM deployment uses `compose.v2.nim.yml`
and `.env.v2.nim`. One Spark must complete the full image-analysis path by itself;
a second Spark is optional capacity, standby, or evaluation infrastructure.

The first release is image-only. Keep ingest validation, server-side quality checks,
local VLM observation, evidence-bound result assembly, and recapture advice on the
critical path. Reference recognition, RAG, Agent workflows, native video, instrument
adapters, and automatic authenticity conclusions remain outside that path.

Do not publish the gateway, NIM, embedding, database, or administration ports to the
Scout LAN. Only the private-LAN HTTPS ingress may be reachable by Scout. Do not add
legacy sessions, browser-demo, report-administration, or reference-library routes to
the V2 Scout API.

Read these documents before changing or deploying V2:

1. `docs/V2_SCOUT_SPARK_PRODUCT_SCOPE.md`
2. `docs/MODEL_SELECTION_AND_SPARK_RUNTIME_2026-09.md`
3. `docs/V2_SCOUT_SPARK_DEPLOYMENT.md`
4. `docs/V2_SPARK_ACCEPTANCE.md`
5. `docs/GITHUB_SPARK_QUICKSTART.md`
6. `scout-android/README.md`

## Supported NIM deployment order

For the primary Spark, preserve this order:

```text
v2-nim-install
→ review .env.v2.nim, licenses, storage, private IP and hostname
→ v2-nim-list-profiles with a protected NGC key file on the target GB10
→ freeze the selected profile and all model identities
→ v2-nim-prepare-online
→ confirm temporary Registry credentials were cleared and block public egress
→ v2-nim-preflight
→ v2-nim-start
→ v2-nim-health
→ v2-nim-export-ca
→ v2-nim-enroll
→ v2-nim-smoke with authorized multi-view images
→ complete V2_SPARK_ACCEPTANCE.md
```

Qwen3.6 NIM `1.7.1-variant` predates the NIM VLM 2.1.1 keyless threshold. Registry
authentication and model-cache preparation therefore belong only in the approved
online window. The repository scripts must use their isolated temporary Docker
credential directory and clean it on every exit. Never store an NGC key in
`.env.v2.nim`, Compose, Git, a runtime container, shell history, or an agent prompt.

`v2-nim-start` runs the strict preflight again. A passing preflight records frozen
configuration and deployment-boundary checks; `v2-nim-health` records readiness;
only a successful multi-image `v2-nim-smoke` records an accepted local completion.
The application receipt is configuration-bound evidence, not an independent
attestation of the container or GPU. Target-Spark performance,
offline behavior, Android connectivity, and hardware identity still require the
acceptance evidence described in `docs/V2_SPARK_ACCEPTANCE.md`.

The V2 `compose.v2.yml`/vLLM path is an explicit compatibility fallback, not the
recommended pilot baseline. `compose.v2.lab.yml` is for isolated candidate-model and
A/B work. Legacy V1 single- and dual-Spark commands are maintenance paths and must not
be used for a V2 deployment unless the operator explicitly requests them.

## Model policy

- Qwen3.6-35B-A3B through the DGX-Spark-specific NVIDIA NIM is the current V2 image
  baseline. Preserve the exact container digest, compatible profile ID, served model
  name, source name, and application commit in deployment evidence.
- The exact Qwen3.6 `1.7.1-variant` does not support Docker's custom `-u` option.
  Preserve the image-defined user and its private writable NIM cache; do not copy a
  generic NIM hardening recipe that adds `user:` or makes this cache read-only.
- Qwen3.8 is a later Chinese multimodal challenger. Do not describe it as
  DGX-Spark-optimized until an official compatible profile or target-machine evidence
  demonstrates that exact claim.
- Nemotron 3 Nano Omni is a native-video candidate. Its official model card identifies
  an English-language boundary, so Chinese ceramic output and faithful translation
  require explicit expert evaluation.
- Run large A/B candidates sequentially on one Spark, or isolate them on the optional
  second Spark. Never infer pooled 256 GB memory or distributed inference from owning
  two 128 GB systems.
- Do not promote a candidate from a machine scorecard alone. Freeze input hashes,
  prompt, schema, model/runtime identity, and comparison rules; require domain and
  model-engineering review.

## Repository-only reproduction

On a development computer, this path exercises deterministic code only:

1. Run `./scripts/reproduce-demo.sh --check-only`.
2. During an approved dependency-install window, run
   `./scripts/reproduce-demo.sh --install`.
3. Confirm `http://127.0.0.1:8088/api/health`.
4. Run the repository checks documented in `docs/GITHUB_SPARK_QUICKSTART.md`.

Report this as deterministic/degraded reproduction. Bundled media and instrument
values are synthetic or replay fixtures; no DGX Spark, GB10, NIM, GPU, offline, sensor,
or scientific validation is implied.

## Useful V2 commands

```bash
make v2-nim-install
make v2-nim-list-profiles NIM_PROFILE_ARGS="--allow-network --ngc-key-file /secure/ngc_api_key"
make v2-nim-prepare-online NIM_PREPARE_ARGS="--ngc-key-file /secure/ngc_api_key"
make v2-nim-preflight
make v2-nim-start
make v2-nim-health
make v2-nim-export-ca
make v2-nim-enroll SCOUT_NAME="Scout 01" SCOUT_DEVICE_ARGS="--output runtime/provisioning/scout-01.json"
make v2-nim-smoke SCOUT_SMOKE_ARGS="--provisioning ... --ca-cert ... --capture FRONT=... --capture BACK=... --capture BASE=..."
```

Optional fallback and evaluation commands are documented in
`docs/V2_SCOUT_SPARK_DEPLOYMENT.md`; do not substitute them silently for the NIM path.

## Safety boundaries

- Never read, print, commit, upload, or paste private runtime configuration such as
  `.env`, `.env.v2`, `.env.v2.nim`, `.env.v2.lab`, `secrets/`, `runtime/`, raw artifact
  media, customer metadata, access tokens, device credentials, or service keys.
  Versioned `*.example` templates are public configuration contracts and may be read.
- Do not install drivers, upgrade DGX OS, change network interfaces, firewall rules,
  users, systemd, storage retention, or customer backups unless the operator explicitly
  authorizes that exact action.
- Do not claim single- or dual-DGX-Spark, GB10, GPU, VLM, NIM, offline-network,
  Android, or scientific-instrument validation without evidence from the target
  machine or machines.
- RGB images may support visible observations, capture-quality findings, comparison,
  and recapture advice. They cannot establish authenticity, exact date, kiln, maker,
  price, grade, provenance, or a legal conclusion.
- Keep `reference_library_used=false`, `rag_used=false`, and `agent_used=false` unless
  those subsystems were separately authorized, configured, and evidenced.
- Preserve user data and unrelated changes. Use repository scripts and Make targets;
  do not improvise production commands that bypass their checks.

## Evidence states

- **CODE_VERIFIED**: static checks and tests passed on a development computer.
- **DEPLOYMENT_READY**: configuration and scripts are prepared, but the customer
  hardware has not completed acceptance.
- **HARDWARE_VERIFIED**: target Spark identity, frozen runtime, health, and real local
  completion evidence passed.
- **PILOT_ACCEPTED**: Android Scout, private network, restart/offline/failure cases,
  authorized images, and named acceptance owners passed the signed checklist.

Never collapse these states into a generic claim that the system is “verified.”
