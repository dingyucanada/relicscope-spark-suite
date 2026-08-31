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

.PHONY: help require-role require-archive install prefetch preflight start stop restart \
	health status backup restore package package-offline install-systemd remove-systemd check \
	demo demo-install demo-check demo-media-check demo-media-smoke demo-media-generate test \
	accept-single-spark ab-single-spark \
	reference-scaffold reference-verify reference-import reference-build reference-evaluate reference-seal reference-status

help:
	@printf '%s\n' \
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
