# RelicScope repository guide for Codex

## Objective

Help an authorized operator reproduce, test, or maintain the RelicScope Spark Suite without weakening its scientific, privacy, or deployment boundaries.

## First-run path

1. Read `README.md` and `docs/GITHUB_SPARK_QUICKSTART.md`.
2. Run `./scripts/reproduce-demo.sh --check-only`.
3. For a fresh clone in an approved online preparation window, run `./scripts/reproduce-demo.sh --install`.
4. Confirm `http://127.0.0.1:8088/api/health` before reporting success.
5. State that local mode is deterministic and degraded: instrument values are demo/replay data, and no real Spark GPU or sensor validation is implied.

## Useful commands

```bash
make demo-install
make demo
make demo-check
make test
openspec validate build-relicscope-dual-spark-demo --strict
```

For two-node deployment, read `docs/DUAL_SPARK_DEPLOYMENT.md` and preserve its order: install → edit `.env` and network-only check → approved online prefetch → restore offline flags → full preflight → start → health. Use the documented fixed entry points; never skip directly to start on a fresh Spark.

## Safety boundaries

- Never read, print, commit, upload, or paste `.env`, `secrets/`, `runtime/`, raw artifact media, tokens, or service keys.
- Do not install drivers, change network interfaces, firewall rules, users, systemd, or storage retention unless the operator explicitly authorizes that exact action.
- Do not claim dual-DGX-Spark, GPU, VLM, offline-network, or scientific-instrument validation without evidence from the target machines.
- Do not turn RGB image/video observations into authenticity, exact dating, kiln, author, price, grading, or legal conclusions.
- Preserve existing user data and unrelated changes. Use the repository scripts and Make targets instead of inventing production commands.

## Definition of local reproduction

- JavaScript syntax and deployment checks pass.
- The Python environment installs from `requirements.txt`.
- The local service starts on loopback.
- `/api/health` responds.
- `make demo-media-check` verifies the bundled synthetic fixture and `make demo-media-smoke` completes the image/video/report/integrity path.
- The browser can run the deterministic P01 workflow and shows `DEMO/SYNTHETIC` and degraded/local runtime boundaries.

Two-node reproduction has additional acceptance criteria in `docs/DUAL_SPARK_DEPLOYMENT.md` and remains a separate hardware validation.
