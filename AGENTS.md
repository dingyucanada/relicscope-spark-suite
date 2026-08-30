# RelicScope repository guide for Codex

## Objective

Help an authorized operator reproduce, test, or maintain the RelicScope Spark Suite without weakening its scientific, privacy, or deployment boundaries.

## First-run paths

Always read `README.md` and `docs/GITHUB_SPARK_QUICKSTART.md` first.

For repository-only reproduction on a development computer:

1. Run `./scripts/reproduce-demo.sh --check-only`.
2. In an approved online dependency-install window, run `./scripts/reproduce-demo.sh --install`.
3. Confirm `http://127.0.0.1:8088/api/health`.
4. Report this as deterministic/degraded reproduction only. Instrument values are demo/replay data and no DGX Spark GPU or sensor validation is implied.

For the v1.2.0 default product path on one authorized DGX Spark:

1. Read `docs/SINGLE_SPARK_GPU_DEPLOYMENT.md` completely.
2. Preserve the order: install → review `.env` and model terms → approved online prefetch → restore offline flags → start → health → live acceptance.
3. Use `make accept-single-spark` to prove that image, native-video, and report calls used the configured local GPU model.
4. Use `make ab-single-spark` only after both model caches are approved. It runs frozen-input Qwen/Nemotron comparison sequentially and restores Qwen on the successful path.
5. Keep Qwen3-VL as the default unless the generated scorecard passes machine gates and the required domain and model-engineering experts approve promotion.

## Useful commands

```bash
make demo-install
make demo
make demo-check
make test
make check

make install ROLE=single INSTALL_ARGS="--generate-key"
make prefetch ROLE=single
make start ROLE=single
make health ROLE=single
make accept-single-spark
make ab-single-spark
```

Two-node deployment is a secondary expansion, not the v1.2.0 default. If explicitly requested, read `docs/DUAL_SPARK_DEPLOYMENT.md` and preserve its documented per-node order. Never infer dual-node or distributed-model validation from a successful single-Spark run.

## Safety boundaries

- Never read, print, commit, upload, or paste `.env`, `secrets/`, `runtime/`, raw artifact media, tokens, or service keys.
- Do not install drivers, change network interfaces, firewall rules, users, systemd, or storage retention unless the operator explicitly authorizes that exact action.
- Do not claim single- or dual-DGX-Spark, GPU, VLM, native-video, offline-network, or scientific-instrument validation without evidence from the target machine or machines.
- Do not turn RGB image/video observations into authenticity, exact dating, kiln, author, price, grading, or legal conclusions.
- Treat Qwen3-VL as the current Chinese ceramic image/video engineering baseline. Treat Nemotron 3 Nano Omni as a native-video candidate with an NVIDIA Spark playbook and an `English only` model-card language boundary; never auto-promote it from one run or a machine scorecard.
- Never run the two large A/B models concurrently on one Spark. Preserve frozen input hashes and the sequential comparison design.
- Preserve existing user data and unrelated changes. Use the repository scripts and Make targets instead of inventing production commands.

## Definition of local reproduction

- JavaScript syntax and deployment checks pass.
- The Python environment installs from `requirements.txt`.
- The local service starts on loopback.
- `/api/health` responds.
- `make demo-media-check` verifies the bundled synthetic fixture and `make demo-media-smoke` completes the image/video/report/integrity path.
- The browser can run the deterministic P01 workflow and shows `DEMO/SYNTHETIC` and degraded/local runtime boundaries.

Single-Spark GPU acceptance additionally requires the evidence in `runtime/acceptance/` described by `docs/SINGLE_SPARK_GPU_DEPLOYMENT.md`. Sequential A/B evidence belongs in `runtime/model-ab/`; machine eligibility never replaces expert review. Two-node reproduction has separate criteria in `docs/DUAL_SPARK_DEPLOYMENT.md` and remains an optional, independent hardware validation.
