SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

ROLE ?=
INSTALL_ARGS ?=
PREFLIGHT_ARGS ?=
START_ARGS ?=
HEALTH_ARGS ?=
PACKAGE_ARGS ?=
BACKUP_ARGS ?=
RESTORE_ARGS ?=
SYSTEMD_ARGS ?=
ACCEPT_ARGS ?=
REFERENCE_ARGS ?=
REFERENCE_SCAFFOLD_DIR ?= runtime/data/reference-library-intake
PRIVATE_ARTWORK_ARCHIVE ?=
PRIVATE_ARTWORK_BATCH ?=
PRIVATE_ARTWORK_ARGS ?=
V2_ENV_FILE ?= .env.v2
V2_COMPOSE_FILE ?= compose.v2.yml
V2_NIM_ENV_FILE ?= .env.v2.nim
V2_LAB_ENV_FILE ?= .env.v2.lab
NIM_PREPARE_ARGS ?=
NIM_PROFILE_ARGS ?=
V2_BACKUP_ARGS ?=
V2_RESTORE_ARGS ?=
V2_LAB_PREPARE_ARGS ?=
V2_LAB_BENCHMARK_ARGS ?=
SCOUT_NAME ?= RelicScope Scout
SCOUT_SERVER_URL ?= https://scout.spark.local:8443
SCOUT_DEVICE_ARGS ?=
SCOUT_SMOKE_ARGS ?=

.PHONY: help require-role require-archive install prefetch preflight start stop restart \
	health status backup restore package package-offline install-systemd remove-systemd check \
	demo demo-install demo-check demo-media-check demo-media-smoke demo-media-generate test \
	console console-install console-check console-smoke \
	accept-single-spark ab-single-spark \
	reference-scaffold reference-verify reference-import reference-build reference-evaluate reference-seal reference-status \
	private-artwork-audit private-artwork-import require-private-artwork-archive \
	v2-install v2-prepare-online v2-preflight v2-start v2-stop v2-health \
	v2-enroll v2-export-ca v2-smoke v2-backup v2-restore \
	v2-nim-install v2-nim-list-profiles v2-nim-prepare-online v2-nim-preflight \
	v2-nim-start v2-nim-stop v2-nim-health v2-nim-enroll v2-nim-export-ca v2-nim-smoke \
	v2-nim-backup v2-nim-restore \
	v2-lab-install v2-lab-prepare-online v2-lab-preflight v2-lab-start \
	v2-lab-stop v2-lab-health v2-lab-benchmark

help:
	@printf '%s\n' \
	  'RelicScope V2: Android Scout + local DGX Spark appliance' \
	  '' \
	  'Recommended: NVIDIA NIM + Qwen3.6-35B-A3B on one DGX Spark' \
	  '' \
	  '  make v2-nim-install' \
	  '  make v2-nim-list-profiles NIM_PROFILE_ARGS="--allow-network --ngc-key-file /secure/ngc_api_key"' \
	  '  make v2-nim-prepare-online NIM_PREPARE_ARGS="--ngc-key-file /secure/ngc_api_key"' \
	  '  make v2-nim-start                     # strict offline preflight, then start' \
	  '  make v2-nim-health' \
	  '  make v2-nim-backup V2_BACKUP_ARGS="--output-dir /absolute/backup"' \
	  '  make v2-nim-restore V2_RESTORE_ARGS="--archive /absolute/backup.tar.gz --confirm-restore"' \
	  '' \
	  'Alternative vLLM runtime:' \
	  '' \
	  '  make v2-install                       # initialize a fresh Spark without downloading' \
	  '  make v2-prepare-online                # explicit approved model/container download window' \
	  '  make v2-preflight                     # verify target Spark and frozen runtime inputs' \
	  '  make v2-start                         # start HTTPS gateway + local VLM' \
	  '  make v2-enroll SCOUT_NAME="Scout 01" SCOUT_DEVICE_ARGS="--output runtime/provisioning/scout-01.json"' \
	  '  make v2-export-ca                     # export the local CA for Android trust setup' \
	  '  make v2-smoke SCOUT_SMOKE_ARGS="..."  # real capture/job/result API test' \
	  '  make private-artwork-audit PRIVATE_ARTWORK_ARCHIVE=/private/data.zip' \
	  '  make private-artwork-import PRIVATE_ARTWORK_ARCHIVE=/private/data.zip PRIVATE_ARTWORK_BATCH=batch-001' \
	  '  make v2-backup V2_BACKUP_ARGS="--output-dir /absolute/backup"' \
	  '  make v2-restore V2_RESTORE_ARGS="--archive /absolute/backup.tar.gz --confirm-restore"' \
	  '' \
	  'Second Spark: isolated candidate-model / evaluation node' \
	  '' \
	  '  make v2-lab-install' \
	  '  make v2-lab-prepare-online            # explicit approved download window' \
	  '  make v2-lab-start                     # preflight + offline start' \
	  '  make v2-lab-health' \
	  '  make v2-lab-benchmark V2_LAB_BENCHMARK_ARGS="..."' \
	  '' \
	  'RelicScope single-Spark operations (dual-node expansion remains available)' \
	  '' \
	  '  make install ROLE=single INSTALL_ARGS="--generate-key"' \
	  '  make prefetch ROLE=single             # cache Qwen baseline + Nemotron candidate' \
	  '  make start ROLE=single                # one-Spark GPU system, Qwen baseline' \
	  '  make accept-single-spark              # prove live GPU image/video/report runs' \
	  '  make ab-single-spark                  # sequential frozen-input Qwen/Nemotron A/B' \
	  '  make reference-scaffold               # create blank 50 + 10 controlled-data intake sheets' \
	  '  make reference-verify                 # validate 50-item controlled manifest/media' \
	  '  make reference-import                 # create integrity-bound metadata index' \
	  '  make reference-build                  # build local Qwen3-VL image embeddings' \
	  '  make reference-evaluate               # evaluate held-out reshoots/open-set negatives' \
	  '  make reference-seal                   # seal held-out calibration thresholds' \
	  '  make reference-status                 # show files/hashes and deployment gate' \
	  '  make install-systemd ROLE=single      # optional boot unit; install after full readiness' \
	  '' \
	  'Dual-node expansion:' \
	  '  make install ROLE=spark-b INSTALL_ARGS="--generate-key"' \
	  '  make install ROLE=spark-a INSTALL_ARGS="--service-key /secure/service_api_key"' \
	  '  make prefetch ROLE=spark-a              # approved online preparation only' \
	  '  make preflight ROLE=spark-a' \
	  '  make start ROLE=spark-a' \
	  '  make health ROLE=spark-b' \
	  '  make stop ROLE=spark-b' \
	  '  make backup ROLE=spark-b' \
	  '  make restore ROLE=spark-b ARCHIVE=/absolute/backup.tar.gz' \
	  '  make package ROLE=all' \
	  '  make package-offline ROLE=spark-a' \
	  '  make install-systemd ROLE=spark-a       # run as root; template only until invoked' \
	  '  make demo-install                       # first deterministic local demo' \
	  '  make demo                               # restart an installed local demo' \
	  '  make demo-check                         # repository checks without starting' \
	  '  make demo-media-check                   # verify bundled synthetic fixture' \
	  '  make demo-media-smoke                   # temporary headless media closed loop' \
	  '  make demo-media-generate                # optional; requires ffmpeg' \
	  '  make test                               # run tests in the local .venv' \
	  '  make check'

v2-nim-install:
	V2_ENV_FILE="$(abspath $(V2_NIM_ENV_FILE))" \
	V2_ENV_TEMPLATE="$(abspath .env.v2.nim.example)" ./deploy/v2-install.sh

v2-nim-list-profiles:
	V2_ENV_FILE="$(abspath $(V2_NIM_ENV_FILE))" ./deploy/v2-nim-list-profiles.sh \
		$(NIM_PROFILE_ARGS)

v2-nim-prepare-online:
	V2_ENV_FILE="$(abspath $(V2_NIM_ENV_FILE))" ./deploy/v2-nim-prepare-online.sh \
		--allow-network $(NIM_PREPARE_ARGS)

v2-nim-preflight:
	V2_ENV_FILE="$(abspath $(V2_NIM_ENV_FILE))" ./deploy/v2-nim-preflight.sh

v2-nim-start: v2-nim-preflight
	docker compose --env-file "$(V2_NIM_ENV_FILE)" -f compose.v2.nim.yml up -d --no-build --pull never

v2-nim-stop:
	docker compose --env-file "$(V2_NIM_ENV_FILE)" -f compose.v2.nim.yml down

v2-nim-health:
	V2_ENV_FILE="$(abspath $(V2_NIM_ENV_FILE))" \
	V2_COMPOSE_FILE="$(abspath compose.v2.nim.yml)" ./deploy/v2-health.sh

v2-nim-enroll:
	$(MAKE) v2-enroll V2_ENV_FILE="$(V2_NIM_ENV_FILE)" \
		SCOUT_NAME="$(SCOUT_NAME)" SCOUT_SERVER_URL="$(SCOUT_SERVER_URL)" \
		SCOUT_DEVICE_ARGS="$(SCOUT_DEVICE_ARGS)"

v2-nim-export-ca:
	$(MAKE) v2-export-ca V2_ENV_FILE="$(V2_NIM_ENV_FILE)"

v2-nim-smoke:
	$(MAKE) v2-smoke SCOUT_SMOKE_ARGS="$(SCOUT_SMOKE_ARGS)"

v2-nim-backup:
	V2_ENV_FILE="$(abspath $(V2_NIM_ENV_FILE))" \
	V2_COMPOSE_FILE="$(abspath compose.v2.nim.yml)" ./deploy/v2-backup.sh $(V2_BACKUP_ARGS)

v2-nim-restore:
	V2_ENV_FILE="$(abspath $(V2_NIM_ENV_FILE))" \
	V2_COMPOSE_FILE="$(abspath compose.v2.nim.yml)" ./deploy/v2-restore.sh $(V2_RESTORE_ARGS)

v2-install:
	V2_ENV_FILE="$(abspath $(V2_ENV_FILE))" ./deploy/v2-install.sh

v2-prepare-online:
	V2_ENV_FILE="$(abspath $(V2_ENV_FILE))" ./deploy/v2-prepare-online.sh --allow-network

v2-preflight:
	V2_ENV_FILE="$(abspath $(V2_ENV_FILE))" ./deploy/v2-preflight.sh

v2-start: v2-preflight
	docker compose --env-file "$(V2_ENV_FILE)" -f compose.v2.yml up -d --no-build --pull never

v2-stop:
	docker compose --env-file "$(V2_ENV_FILE)" -f compose.v2.yml down

v2-health:
	V2_ENV_FILE="$(abspath $(V2_ENV_FILE))" ./deploy/v2-health.sh

v2-enroll:
	@test -x .venv-v2/bin/python || { printf '%s\n' 'Run make v2-prepare-online first.' >&2; exit 2; }
	@test -f "$(abspath $(V2_ENV_FILE))" || { printf '%s\n' '.env.v2 is missing.' >&2; exit 2; }
	@data_dir="$$(.venv-v2/bin/python deploy/read-v2-env.py \
	    --file "$(abspath $(V2_ENV_FILE))" --key RELICSCOPE_DATA_HOST_DIR \
	    --default ./runtime/v2-data)"; \
	  case "$$data_dir" in /*) ;; *) data_dir="$(CURDIR)/$$data_dir" ;; esac; \
	  RELICSCOPE_DATA_DIR="$$data_dir" .venv-v2/bin/python scripts/scout-device.py enroll \
	    --name "$(SCOUT_NAME)" --server-url "$(SCOUT_SERVER_URL)" $(SCOUT_DEVICE_ARGS)

v2-export-ca:
	V2_ENV_FILE="$(abspath $(V2_ENV_FILE))" ./deploy/export-scout-ca.sh

v2-smoke:
	@test -x .venv-v2/bin/python || { printf '%s\n' 'Run make v2-prepare-online first.' >&2; exit 2; }
	.venv-v2/bin/python scripts/scout-smoke.py $(SCOUT_SMOKE_ARGS)

v2-backup:
	V2_ENV_FILE="$(abspath $(V2_ENV_FILE))" \
	V2_COMPOSE_FILE="$(abspath $(V2_COMPOSE_FILE))" ./deploy/v2-backup.sh $(V2_BACKUP_ARGS)

v2-restore:
	V2_ENV_FILE="$(abspath $(V2_ENV_FILE))" \
	V2_COMPOSE_FILE="$(abspath $(V2_COMPOSE_FILE))" ./deploy/v2-restore.sh $(V2_RESTORE_ARGS)

v2-lab-install:
	V2_LAB_ENV_FILE="$(abspath $(V2_LAB_ENV_FILE))" ./deploy/v2-lab-install.sh

v2-lab-prepare-online:
	V2_LAB_ENV_FILE="$(abspath $(V2_LAB_ENV_FILE))" ./deploy/v2-lab-prepare-online.sh --allow-network $(V2_LAB_PREPARE_ARGS)

v2-lab-preflight:
	V2_LAB_ENV_FILE="$(abspath $(V2_LAB_ENV_FILE))" ./deploy/v2-lab-preflight.sh

v2-lab-start: v2-lab-preflight
	docker compose --env-file "$(V2_LAB_ENV_FILE)" -f compose.v2.lab.yml up -d --no-build --pull never

v2-lab-stop:
	docker compose --env-file "$(V2_LAB_ENV_FILE)" -f compose.v2.lab.yml down

v2-lab-health:
	V2_LAB_ENV_FILE="$(abspath $(V2_LAB_ENV_FILE))" ./deploy/v2-lab-health.sh

v2-lab-benchmark:
	@test -x .venv-v2/bin/python || { printf '%s\n' 'Run make v2-lab-prepare-online first.' >&2; exit 2; }
	.venv-v2/bin/python scripts/benchmark-scout-vlm.py $(V2_LAB_BENCHMARK_ARGS)

demo:
	./scripts/reproduce-demo.sh

demo-install:
	./scripts/reproduce-demo.sh --install

demo-check:
	./scripts/reproduce-demo.sh --check-only

demo-media-check:
	@test -x .venv/bin/python || { printf '%s\n' 'Project .venv is required.' >&2; exit 2; }
	.venv/bin/python scripts/verify-demo-media.py

demo-media-smoke:
	./scripts/demo-media-smoke.sh

demo-media-generate:
	@test -x .venv/bin/python || { printf '%s\n' 'Project .venv is required.' >&2; exit 2; }
	.venv/bin/python scripts/generate-demo-media.py

# Stable names for the local engineering preview. It is a FastAPI application;
# opening app/static/index.html directly cannot provide the required API.
console: demo

console-install: demo-install

console-check: demo-check

console-smoke: demo-media-smoke

accept-single-spark:
	./deploy/single-spark-accept.sh $(ACCEPT_ARGS)

ab-single-spark:
	./deploy/single-spark-ab.sh

reference-scaffold:
	@test -x .venv/bin/python || { printf '%s\n' 'Project .venv is required.' >&2; exit 2; }
	.venv/bin/python scripts/scaffold-reference-library.py "$(REFERENCE_SCAFFOLD_DIR)"

reference-verify:
	./deploy/reference-library.sh verify $(REFERENCE_ARGS)

reference-import:
	./deploy/reference-library.sh import $(REFERENCE_ARGS)

reference-build:
	./deploy/reference-library.sh build $(REFERENCE_ARGS)

reference-evaluate:
	./deploy/reference-library.sh evaluate $(REFERENCE_ARGS)

reference-seal:
	./deploy/reference-library.sh seal $(REFERENCE_ARGS)

reference-status:
	./deploy/reference-library.sh status $(REFERENCE_ARGS)

require-private-artwork-archive:
	@test -n "$(PRIVATE_ARTWORK_ARCHIVE)" || { \
	  printf '%s\n' 'PRIVATE_ARTWORK_ARCHIVE=/absolute/path/archive.zip is required' >&2; \
	  exit 2; \
	}
	@test -f "$(PRIVATE_ARTWORK_ARCHIVE)" || { \
	  printf '%s\n' 'PRIVATE_ARTWORK_ARCHIVE must be a readable regular file' >&2; \
	  exit 2; \
	}

private-artwork-audit: require-private-artwork-archive
	@python_bin=.venv-v2/bin/python; \
	  test -x "$$python_bin" || python_bin=.venv/bin/python; \
	  test -x "$$python_bin" || { printf '%s\n' 'Run make v2-nim-prepare-online or make console-install first.' >&2; exit 2; }; \
	  "$$python_bin" scripts/import-private-artwork-archive.py \
	    "$(PRIVATE_ARTWORK_ARCHIVE)" $(PRIVATE_ARTWORK_ARGS)

private-artwork-import: require-private-artwork-archive
	@test -n "$(PRIVATE_ARTWORK_BATCH)" || { \
	  printf '%s\n' 'PRIVATE_ARTWORK_BATCH is required' >&2; \
	  exit 2; \
	}
	@python_bin=.venv-v2/bin/python; \
	  test -x "$$python_bin" || python_bin=.venv/bin/python; \
	  test -x "$$python_bin" || { printf '%s\n' 'Run make v2-nim-prepare-online or make console-install first.' >&2; exit 2; }; \
	  "$$python_bin" scripts/import-private-artwork-archive.py \
	    "$(PRIVATE_ARTWORK_ARCHIVE)" --import-batch "$(PRIVATE_ARTWORK_BATCH)" \
	    $(PRIVATE_ARTWORK_ARGS)

test:
	@test -x .venv/bin/python || { printf '%s\n' 'Run make demo-install first.' >&2; exit 2; }
	.venv/bin/python -m pytest -q

require-role:
	@if [[ "$(ROLE)" != "spark-a" && "$(ROLE)" != "spark-b" && "$(ROLE)" != "single" && "$(ROLE)" != "all" ]]; then \
	  printf 'ROLE must be spark-a, spark-b, single, or all\n' >&2; exit 2; \
	fi

require-archive:
	@if [[ -z "$(ARCHIVE)" ]]; then printf 'ARCHIVE=/absolute/path is required\n' >&2; exit 2; fi

install: require-role
	@if [[ "$(ROLE)" == "all" ]]; then printf 'install must run separately on each physical node\n' >&2; exit 2; fi
	./deploy/install.sh --role "$(ROLE)" $(INSTALL_ARGS)

prefetch: require-role
	./deploy/prefetch.sh --role "$(ROLE)"

preflight: require-role
	./deploy/service-control.sh preflight --role "$(ROLE)" $(PREFLIGHT_ARGS)

start: require-role
	./deploy/service-control.sh start --role "$(ROLE)" $(START_ARGS)

stop: require-role
	./deploy/service-control.sh stop --role "$(ROLE)"

restart: require-role
	./deploy/service-control.sh restart --role "$(ROLE)" $(START_ARGS)

health: require-role
	./deploy/service-control.sh health --role "$(ROLE)" $(HEALTH_ARGS)

status: require-role
	./deploy/service-control.sh status --role "$(ROLE)"

backup: require-role
	./deploy/backup.sh --role "$(ROLE)" $(BACKUP_ARGS)

restore: require-role require-archive
	./deploy/restore.sh --role "$(ROLE)" --archive "$(ARCHIVE)" $(RESTORE_ARGS)

package: require-role
	./deploy/package.sh --role "$(ROLE)" $(PACKAGE_ARGS)

package-offline: require-role
	./deploy/package.sh --role "$(ROLE)" --offline $(PACKAGE_ARGS)

install-systemd: require-role
	./deploy/install-systemd.sh --role "$(ROLE)" $(SYSTEMD_ARGS)

remove-systemd: require-role
	./deploy/install-systemd.sh --role "$(ROLE)" --remove

check:
	./scripts/check-deployment.sh
