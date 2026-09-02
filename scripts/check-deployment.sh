#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

failures=0
checked=0
while IFS= read -r -d '' script; do
  checked=$((checked + 1))
  if ! bash -n "$script"; then failures=$((failures + 1)); fi
  if ! grep -Eq '^set -[^#]*E[^#]*e[^#]*u[^#]*o[[:space:]]+pipefail|^set -Eeuo pipefail' "$script"; then
    printf 'ERROR: strict shell mode is missing: %s\n' "$script" >&2
    failures=$((failures + 1))
  fi
done < <(find "${PROJECT_DIR}/deploy" "${PROJECT_DIR}/scripts" -type f -name '*.sh' -print0 | sort -z)

if command -v shellcheck >/dev/null 2>&1; then
  while IFS= read -r -d '' script; do
    shellcheck -x "$script" || failures=$((failures + 1))
  done < <(find "${PROJECT_DIR}/deploy" "${PROJECT_DIR}/scripts" -type f -name '*.sh' -print0 | sort -z)
else
  printf '%s\n' 'INFO: shellcheck is not installed; bash -n and policy checks were run.'
fi

managed_path_validator="${PROJECT_DIR}/deploy/validate-v2-managed-paths.py"
if ! python3 - "${managed_path_validator}" "${PROJECT_DIR}" <<'PY'
import pathlib
import subprocess
import sys
import tempfile

validator = pathlib.Path(sys.argv[1])
repository = pathlib.Path(sys.argv[2])


def run(project: pathlib.Path, paths: list[pathlib.Path]) -> int:
    return subprocess.run(
        [
            sys.executable,
            str(validator),
            "--project-root",
            str(project),
            *(str(path) for path in paths),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


with tempfile.TemporaryDirectory(prefix=".managed-path-test-", dir=repository) as raw:
    root = pathlib.Path(raw)
    project = root / "project"
    project.mkdir()
    hf = project / "managed" / "hf"
    vllm = project / "managed" / "vllm"
    caddy_data = project / "managed" / "caddy-data"
    caddy_config = project / "managed" / "caddy-config"
    secret = project / "secrets" / "api-key"
    for path in (hf, vllm, caddy_data, caddy_config, secret.parent):
        path.mkdir(parents=True, exist_ok=True)
    secret.write_text("test", encoding="ascii")
    valid = [hf, vllm, caddy_data, caddy_config, secret]
    if run(project, valid) != 0:
        raise SystemExit("managed-path validator rejected separate regular paths")

    nested = hf / "nested"
    nested.mkdir()
    if run(project, [hf, nested, caddy_data, caddy_config, secret]) == 0:
        raise SystemExit("managed-path validator accepted nested paths")

    symlink = project / "managed" / "hf-link"
    symlink.symlink_to(hf, target_is_directory=True)
    if run(project, [symlink, vllm, caddy_data, caddy_config, secret]) == 0:
        raise SystemExit("managed-path validator accepted a symlink path")

    linked_parent = project / "linked-parent"
    linked_parent.symlink_to(project / "managed", target_is_directory=True)
    if run(
        project,
        [linked_parent / "hf", vllm, caddy_data, caddy_config, secret],
    ) == 0:
        raise SystemExit("managed-path validator accepted a symlink ancestor")
PY
then
  printf '%s\n' 'ERROR: V2 managed-path isolation tests failed.' >&2
  failures=$((failures + 1))
fi

for lab_path_script in \
  deploy/v2-lab-install.sh deploy/v2-lab-prepare-online.sh \
  deploy/v2-lab-preflight.sh; do
  if ! grep -Fq 'deploy/validate-v2-managed-paths.py' "${PROJECT_DIR}/${lab_path_script}"; then
    printf 'ERROR: lab managed-path validator is not enforced by %s\n' "${lab_path_script}" >&2
    failures=$((failures + 1))
  fi
  for required_path in hf_cache vllm_cache caddy_data caddy_config secret_file; do
    if ! grep -Fq "\${${required_path}}" "${PROJECT_DIR}/${lab_path_script}"; then
      printf 'ERROR: %s does not bind %s into path isolation.\n' \
        "${lab_path_script}" "${required_path}" >&2
      failures=$((failures + 1))
    fi
  done
done

for main_path_script in \
  deploy/v2-install.sh deploy/v2-prepare-online.sh deploy/v2-preflight.sh; do
  if ! grep -Fq 'deploy/validate-v2-managed-paths.py' "${PROJECT_DIR}/${main_path_script}"; then
    printf 'ERROR: main managed-path validator is not enforced by %s\n' \
      "${main_path_script}" >&2
    failures=$((failures + 1))
  fi
  if grep -Eq 'safe_managed_dir|assert_separate_paths|python3 - "\$\{project_root\}" "\$@"' \
    "${PROJECT_DIR}/${main_path_script}"; then
    printf 'ERROR: %s still contains a weaker local managed-path validator.\n' \
      "${main_path_script}" >&2
    failures=$((failures + 1))
  fi
done
for required_path in \
  data_dir hf_cache_dir vllm_cache_dir caddy_data_dir caddy_config_dir secret_file; do
  if ! grep -Fq "\${${required_path}}" "${PROJECT_DIR}/deploy/v2-install.sh"; then
    printf 'ERROR: main install does not bind %s into path isolation.\n' \
      "${required_path}" >&2
    failures=$((failures + 1))
  fi
done
for main_path_script in deploy/v2-prepare-online.sh deploy/v2-preflight.sh; do
  for required_path in data_dir hf_cache vllm_cache caddy_data caddy_config secret_file; do
    if ! grep -Fq "\${${required_path}}" "${PROJECT_DIR}/${main_path_script}"; then
      printf 'ERROR: %s does not bind %s into path isolation.\n' \
        "${main_path_script}" "${required_path}" >&2
      failures=$((failures + 1))
    fi
  done
done
if ! python3 - "${PROJECT_DIR}" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
cases = {
    "deploy/v2-install.sh": (
        ("data_dir", "hf_cache_dir", "vllm_cache_dir", "caddy_data_dir", "caddy_config_dir", "secret_file"),
        ("\ninstall -d -m 700 --", "\n  openssl rand -hex 48"),
    ),
    "deploy/v2-prepare-online.sh": (
        ("data_dir", "hf_cache", "vllm_cache", "caddy_data", "caddy_config", "secret_file"),
        ("\ninstall -d -m 700 --", "\n  docker pull \"${requested}\""),
    ),
}
for relative, (variables, mutation_markers) in cases.items():
    source = (root / relative).read_text(encoding="utf-8")
    call = source.find("\nvalidate_managed_paths \\\n")
    if call < 0:
        raise SystemExit(f"{relative}: shared validator call is missing")
    block_end = source.find("\n\n", call)
    block = source[call:block_end]
    missing = [name for name in variables if f'"${{{name}}}"' not in block]
    if missing:
        raise SystemExit(f"{relative}: validator call is missing {missing}")
    for marker in mutation_markers:
        position = source.find(marker)
        if position < 0 or position < call:
            raise SystemExit(
                f"{relative}: managed-path validation must precede {marker.strip()}"
            )
PY
then
  printf '%s\n' 'ERROR: main V2 path validation ordering check failed.' >&2
  failures=$((failures + 1))
fi

if ! grep -Fq -- '--user "$(id -u):$(id -g)"' "${PROJECT_DIR}/deploy/v2-preflight.sh"; then
  printf '%s\n' 'ERROR: main offline cache probe does not use the production host UID:GID.' >&2
  failures=$((failures + 1))
fi

env_reader="${PROJECT_DIR}/deploy/read-v2-env.py"
if ! python3 - "${env_reader}" <<'PY'
import pathlib
import subprocess
import sys
import tempfile

reader = pathlib.Path(sys.argv[1])
with tempfile.TemporaryDirectory() as raw:
    env_file = pathlib.Path(raw) / ".env.v2"
    env_file.write_bytes(
        b"SCOUT_HOSTNAME='scout.spark.local'\r\n"
        b'CADDY_DATA_DIR="./runtime/caddy data"\r\n'
    )
    value = subprocess.run(
        [
            sys.executable,
            str(reader),
            "--file",
            str(env_file),
            "--key",
            "CADDY_DATA_DIR",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if value != "./runtime/caddy data":
        raise SystemExit("safe env reader did not normalize quoted CRLF input")
PY
then
  printf '%s\n' 'ERROR: safe V2 environment reader check failed.' >&2
  failures=$((failures + 1))
fi
for env_consumer in \
  deploy/v2-health.sh deploy/export-scout-ca.sh \
  deploy/v2-backup.sh deploy/v2-restore.sh; do
  if ! grep -Fq 'deploy/read-v2-env.py' "${PROJECT_DIR}/${env_consumer}"; then
    printf 'ERROR: %s does not use the shared safe V2 env reader.\n' \
      "${env_consumer}" >&2
    failures=$((failures + 1))
  fi
done
if ! grep -Fq 'deploy/read-v2-env.py' "${PROJECT_DIR}/Makefile"; then
  printf '%s\n' 'ERROR: Makefile v2-enroll does not use the shared safe V2 env reader.' >&2
  failures=$((failures + 1))
fi

if ! python3 - "${PROJECT_DIR}" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
device = (root / "scripts/scout-device.py").read_text(encoding="utf-8")
for relative in ("deploy/v2-backup.sh", "deploy/v2-restore.sh"):
    source = (root / relative).read_text(encoding="utf-8")
    if '${project_root}/runtime/.v2-maintenance.lock' not in source:
        raise SystemExit(f"{relative}: shared maintenance lock path is missing")
if 'PROJECT_ROOT / "runtime" / ".v2-maintenance.lock"' not in device:
    raise SystemExit("Scout device mutations do not use the shared maintenance lock")
if "fcntl.LOCK_EX | fcntl.LOCK_NB" not in device:
    raise SystemExit("Scout device maintenance lock is not exclusive/nonblocking")
list_branch = device.find('if args.command == "list":')
mutation_lock = device.find("with _maintenance_lock():")
if list_branch < 0 or mutation_lock < 0 or list_branch > mutation_lock:
    raise SystemExit("Scout device list must remain outside the mutation lock")
if "_list_devices_readonly(settings.db_path)" not in device:
    raise SystemExit("Scout device list does not use the read-only database path")
PY
then
  printf '%s\n' 'ERROR: V2 maintenance-lock compatibility check failed.' >&2
  failures=$((failures + 1))
fi

for provenance_key in ingress_caddy_image ingress_caddy_image_id; do
  if ! grep -Fq "\"${provenance_key}\"" "${PROJECT_DIR}/deploy/v2-backup.sh"; then
    printf 'ERROR: backup manifest does not record %s.\n' "${provenance_key}" >&2
    failures=$((failures + 1))
  fi
  if ! grep -Fq "\"${provenance_key}\"" "${PROJECT_DIR}/deploy/v2-restore.sh"; then
    printf 'ERROR: restore does not read %s.\n' "${provenance_key}" >&2
    failures=$((failures + 1))
  fi
done
if ! grep -Fq 'backup_ingress_caddy_image_id' "${PROJECT_DIR}/deploy/v2-restore.sh"; then
  printf '%s\n' 'ERROR: restore does not compare the ingress Caddy image ID.' >&2
  failures=$((failures + 1))
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  (
    cd "$PROJECT_DIR"
    docker compose --env-file .env.example -f compose.yml config --quiet
    REFERENCE_EMBEDDING_MODEL_REVISION=0000000000000000000000000000000000000000 \
      docker compose --env-file .env.example -f compose.single.yml config --quiet
    REFERENCE_EMBEDDING_MODEL_REVISION=0000000000000000000000000000000000000000 \
      docker compose --env-file .env.example -f compose.single.yml config --format json \
      | python3 -c '
import json
import sys

config = json.load(sys.stdin)
services = config["services"]
embedding = services["reference-embedding"]
app = services["app"]
assert not embedding.get("ports"), "reference embedding must not publish a port"
assert embedding.get("read_only") is True, "reference embedding root must be read-only"
assert embedding.get("cap_drop") == ["ALL"], "reference embedding capabilities must be dropped"
assert embedding.get("gpus") or embedding.get("deploy"), "reference embedding GPU request is missing"
secret_sources = {
    item if isinstance(item, str) else item.get("source")
    for item in embedding.get("secrets", [])
}
assert "service_api_key" in secret_sources, "reference embedding service key is missing"
assert app["depends_on"]["reference-embedding"]["condition"] == "service_healthy"
assert app["environment"]["REFERENCE_EMBEDDING_BASE_URL"] == "http://reference-embedding:8010/v1"
network_name = next(iter(embedding["networks"]))
assert config["networks"][network_name].get("internal") is True
'

    VISION_MODEL_REVISION=0000000000000000000000000000000000000000 \
      docker compose --env-file .env.v2.example -f compose.v2.yml config --quiet
    VISION_MODEL_REVISION=0000000000000000000000000000000000000000 \
      docker compose --env-file .env.v2.example -f compose.v2.yml config --format json \
      | python3 -c '
import json
import sys

config = json.load(sys.stdin)
services = config["services"]
gateway = services["gateway"]
vision = services["vision"]
ingress = services["ingress"]
assert not gateway.get("ports"), "V2 gateway must not publish a port"
assert not vision.get("ports"), "V2 model must not publish a port"
assert ingress.get("ports"), "V2 HTTPS ingress port is missing"
assert "vision" not in (gateway.get("depends_on") or {}), "gateway must survive model downtime"
assert set(gateway["networks"]) == {"gateway-private", "model-private"}
assert set(vision["networks"]) == {"model-private"}
assert "gateway-private" in set(ingress["networks"])
assert config["networks"]["gateway-private"].get("internal") is True
assert config["networks"]["model-private"].get("internal") is True
assert all(item.get("pull_policy") == "never" for item in services.values())
assert gateway.get("read_only") is True and vision.get("read_only") is True
assert gateway.get("cap_drop") == ["ALL"] and vision.get("cap_drop") == ["ALL"]
assert vision.get("user") == "1000:1000" and ingress.get("user") == "1000:1000"
assert vision.get("ipc") != "host", "V2 model must not share host IPC"
assert vision.get("gpus") or vision.get("deploy"), "V2 model GPU request is missing"
assert "--api-key" not in " ".join(vision.get("command") or []), "API key must not enter argv"
'

    NIM_MODEL_PROFILE=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
      VISION_MODEL_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
      NIM_VLM_IMAGE=nvcr.io/nim/qwen/qwen3.6-35b-a3b@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
      RELICSCOPE_GIT_COMMIT=cccccccccccccccccccccccccccccccccccccccc \
      VISION_MODEL=qwen/qwen3.6-35b-a3b \
      VISION_MODEL_SOURCE=qwen/qwen3.6-35b-a3b \
      NIM_SERVED_MODEL_NAME=qwen/qwen3.6-35b-a3b \
      RELICSCOPE_SCOUT_MAX_IMAGES_PER_JOB=5 \
      NIM_MAX_IMAGES_PER_PROMPT=8 \
      docker compose --env-file .env.v2.nim.example -f compose.v2.nim.yml config --quiet
    NIM_MODEL_PROFILE=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
      VISION_MODEL_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
      NIM_VLM_IMAGE=nvcr.io/nim/qwen/qwen3.6-35b-a3b@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
      RELICSCOPE_GIT_COMMIT=cccccccccccccccccccccccccccccccccccccccc \
      VISION_MODEL=qwen/qwen3.6-35b-a3b \
      VISION_MODEL_SOURCE=qwen/qwen3.6-35b-a3b \
      NIM_SERVED_MODEL_NAME=qwen/qwen3.6-35b-a3b \
      RELICSCOPE_SCOUT_MAX_IMAGES_PER_JOB=5 \
      NIM_MAX_IMAGES_PER_PROMPT=8 \
      docker compose --env-file .env.v2.nim.example -f compose.v2.nim.yml config --format json \
      | python3 -c '
import json
import sys

config = json.load(sys.stdin)
services = config["services"]
gateway = services["gateway"]
vision = services["vision"]
ingress = services["ingress"]

published = {name for name, service in services.items() if service.get("ports")}
assert published == {"ingress"}, "only the HTTPS ingress may publish a host port"
assert ingress["ports"][0].get("host_ip") == "127.0.0.1", "example ingress must bind loopback"
assert set(gateway["networks"]) == {"gateway-private", "model-private"}
assert set(vision["networks"]) == {"model-private"}
assert set(ingress["networks"]) == {"lan-edge", "gateway-private"}
assert config["networks"]["gateway-private"].get("internal") is True
assert config["networks"]["model-private"].get("internal") is True
assert not config["networks"]["lan-edge"].get("internal", False)
assert all(service.get("pull_policy") == "never" for service in services.values())
assert vision.get("user") in (None, ""), "Qwen3.6 Spark NIM must not set a custom user"
assert vision.get("gpus") or vision.get("deploy"), "NIM GPU request is missing"
assert not vision.get("privileged", False), "NIM must not run privileged"
assert vision.get("pid") != "host", "NIM must not share the host PID namespace"
assert vision.get("ipc") != "host", "NIM must not share host IPC"

profile = "a" * 64
image = "nvcr.io/nim/qwen/qwen3.6-35b-a3b@sha256:" + "b" * 64
model = "qwen/qwen3.6-35b-a3b"
nim_env = vision["environment"]
gateway_env = gateway["environment"]
assert vision["image"] == image
assert nim_env["NIM_MODEL_PROFILE"] == profile
assert gateway_env["VISION_MODEL_REVISION"] == profile
assert gateway_env["VISION_RUNTIME_IMAGE"] == image
assert gateway_env["VISION_MODEL"] == gateway_env["VISION_MODEL_SOURCE"] == model
assert gateway_env["VISION_MODEL"] == nim_env["NIM_SERVED_MODEL_NAME"]
gateway_images = int(gateway_env["RELICSCOPE_SCOUT_MAX_IMAGES_PER_JOB"])
nim_images = int(nim_env["NIM_MAX_IMAGES_PER_PROMPT"])
assert 1 <= gateway_images <= nim_images <= 8
assert str(nim_env["NIM_MAX_VIDEOS_PER_PROMPT"]) == "0"
assert str(nim_env["NIM_DISABLE_MODEL_DOWNLOAD"]) == "1"

credential_names = {
    "NGC_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "NVIDIA_API_KEY"
}
for name, service in services.items():
    environment = service.get("environment") or {}
    leaked = credential_names.intersection(environment)
    assert not leaked, f"{name} leaks credential variables into runtime: {sorted(leaked)}"
assert not vision.get("secrets"), "NIM runtime must not receive registry credentials"
gateway_secret_sources = {
    item if isinstance(item, str) else item.get("source")
    for item in gateway.get("secrets", [])
}
assert gateway_secret_sources == {"service_api_key"}
assert "SERVICE_API_KEY" not in gateway_env and "VISION_API_KEY" not in gateway_env
cache = next(item for item in vision.get("volumes", []) if item.get("target") == "/opt/nim/.cache")
assert not cache.get("read_only"), "this NIM variant requires a writable cache mount"
'

    LAB_UID=1000 LAB_GID=1000 \
      RELICSCOPE_LAB_GIT_COMMIT=0000000000000000000000000000000000000000 \
      LAB_MODEL_REVISION=0000000000000000000000000000000000000000 \
      docker compose --env-file .env.v2.lab.example -f compose.v2.lab.yml config --quiet
    LAB_UID=1000 LAB_GID=1000 \
      RELICSCOPE_LAB_GIT_COMMIT=0000000000000000000000000000000000000000 \
      LAB_MODEL_REVISION=0000000000000000000000000000000000000000 \
      docker compose --env-file .env.v2.lab.example -f compose.v2.lab.yml config --format json \
      | python3 -c '
import json
import sys

config = json.load(sys.stdin)
services = config["services"]
model = services["lab-vision"]
ingress = services["lab-ingress"]
assert not model.get("ports"), "lab model must not publish a port"
assert ingress.get("ports"), "lab HTTPS port is missing"
assert config["networks"]["lab-private"].get("internal") is True
assert set(model["networks"]) == {"lab-private"}
assert "lab-private" in set(ingress["networks"])
assert all(item.get("pull_policy") == "never" for item in services.values())
assert model.get("user") == "1000:1000" and ingress.get("user") == "1000:1000"
assert model.get("read_only") is True and ingress.get("read_only") is True
assert model.get("cap_drop") == ["ALL"] and ingress.get("cap_drop") == ["ALL"]
assert model.get("gpus") or model.get("deploy"), "lab model GPU request is missing"
assert model["environment"]["LAB_MODEL_PROFILE"] == "nemotron3-nano-omni"
command = " ".join(model.get("command") or [])
for required_flag in ("--trust-remote-code", "--reasoning-parser nemotron_v3", "--video-pruning-rate"):
    assert required_flag in command, f"Nemotron lab recipe is missing {required_flag}"
assert "--api-key" not in command, "lab API key must not enter argv"
'
  ) || failures=$((failures + 1))
else
  printf '%s\n' 'INFO: Docker Compose is unavailable; compose static parsing was skipped.'
fi

if ! grep -Fq '@allowed path /v1/models /v1/chat/completions' "${PROJECT_DIR}/deploy/Caddyfile.v2-lab"; then
  printf '%s\n' 'ERROR: lab ingress does not allowlist the two required vLLM routes.' >&2
  failures=$((failures + 1))
fi
if ! grep -Fq 'max_size 128MB' "${PROJECT_DIR}/deploy/Caddyfile.v2-lab"; then
  printf '%s\n' 'ERROR: lab ingress request-body limit is missing.' >&2
  failures=$((failures + 1))
fi

for executable in \
  deploy/v2-install.sh deploy/v2-prepare-online.sh deploy/v2-preflight.sh \
  deploy/v2-nim-list-profiles.sh deploy/v2-nim-prepare-online.sh \
  deploy/v2-nim-preflight.sh \
  deploy/v2-health.sh deploy/v2-backup.sh deploy/v2-restore.sh \
  deploy/v2-lab-install.sh deploy/v2-lab-prepare-online.sh \
  deploy/v2-lab-preflight.sh deploy/v2-lab-health.sh \
  deploy/export-scout-ca.sh scripts/scout-device.py \
  scripts/scout-smoke.py scripts/benchmark-scout-vlm.py scout-android/gradlew; do
  if [[ ! -x "${PROJECT_DIR}/${executable}" ]]; then
    printf 'ERROR: required V2 executable bit is missing: %s\n' "$executable" >&2
    failures=$((failures + 1))
  fi
done

for required in \
  .env.v2.example .env.v2.lab.example .env.v2.nim.example \
  compose.v2.yml compose.v2.lab.yml compose.v2.nim.yml \
  app/scout_main.py app/scout/api.py app/scout/service.py app/scout/store.py \
  deploy/Caddyfile.v2 deploy/Caddyfile.v2-lab deploy/v2-install.sh \
  deploy/v2-nim-list-profiles.sh deploy/v2-nim-prepare-online.sh \
  deploy/v2-nim-preflight.sh \
  deploy/v2-prepare-online.sh deploy/v2-preflight.sh deploy/v2-health.sh \
  deploy/v2-backup.sh deploy/v2-restore.sh deploy/v2-lab-install.sh \
  deploy/v2-lab-prepare-online.sh deploy/v2-lab-preflight.sh deploy/v2-lab-health.sh \
  deploy/read-v2-env.py deploy/validate-v2-managed-paths.py \
  docs/V2_SCOUT_SPARK_PRODUCT_SCOPE.md docs/V2_SCOUT_SPARK_DEPLOYMENT.md \
  docs/V2_SPARK_ACCEPTANCE.md docs/V2_SECOND_SPARK_LAB.md docs/V2_BACKUP_RESTORE.md \
  scout-android/gradlew scout-android/gradle/wrapper/gradle-wrapper.jar \
  scout-android/app/src/main/AndroidManifest.xml \
  scout-android/app/src/main/java/ai/relicscope/scout/MainActivity.kt \
  scout-android/app/src/main/java/ai/relicscope/scout/ScoutApplication.kt; do
  if [[ ! -f "${PROJECT_DIR}/${required}" ]]; then
    printf 'ERROR: V2 release file is missing: %s\n' "$required" >&2
    failures=$((failures + 1))
  fi
done

for packaged in \
  '.env.v2.example' '.env.v2.lab.example' '.env.v2.nim.example' 'scout-android' \
  'compose.v2.yml' 'compose.v2.lab.yml' 'compose.v2.nim.yml' \
  'deploy/v2-nim-list-profiles.sh' 'deploy/v2-nim-prepare-online.sh' \
  'deploy/v2-nim-preflight.sh'; do
  if ! grep -Eq "(^|[[:space:]])${packaged//./\\.}([[:space:]]|$)" "${PROJECT_DIR}/deploy/package.sh"; then
    printf 'ERROR: V2 release entry is absent from deploy/package.sh: %s\n' "$packaged" >&2
    failures=$((failures + 1))
  fi
done

if command -v unzip >/dev/null 2>&1; then
  unzip -tqq "${PROJECT_DIR}/scout-android/gradle/wrapper/gradle-wrapper.jar" \
    || failures=$((failures + 1))
else
  printf '%s\n' 'INFO: unzip is unavailable; Gradle Wrapper JAR integrity was not checked.'
fi

for required in \
  Dockerfile.embedding requirements-embedding.lock embedding_server/main.py \
  scripts/import-reference-library.py scripts/build-reference-vector-index.py \
  scripts/evaluate-reference-recognition.py scripts/seal-reference-calibration.py \
  deploy/reference-library.sh docs/REFERENCE_LIBRARY_DEPLOYMENT.md; do
  if [[ ! -f "${PROJECT_DIR}/${required}" ]]; then
    printf 'ERROR: reference deployment file is missing: %s\n' "$required" >&2
    failures=$((failures + 1))
  fi
done
for allowlisted in \
  '!Dockerfile.embedding' '!requirements-embedding.lock' '!embedding_server/**' \
  '!scripts/import-reference-library.py' '!scripts/build-reference-vector-index.py' \
  '!scripts/evaluate-reference-recognition.py' '!scripts/seal-reference-calibration.py'; do
  if ! grep -Fqx "$allowlisted" "${PROJECT_DIR}/.dockerignore"; then
    printf 'ERROR: embedding build input is excluded by .dockerignore: %s\n' "$allowlisted" >&2
    failures=$((failures + 1))
  fi
done
if ! grep -Fqx 'sentence-transformers[image]==5.4.0' "${PROJECT_DIR}/requirements-embedding.lock"; then
  printf '%s\n' 'ERROR: multimodal Sentence Transformers 5.4.0 is not pinned.' >&2
  failures=$((failures + 1))
fi
for copied_cli in \
  scripts/import-reference-library.py scripts/build-reference-vector-index.py \
  scripts/evaluate-reference-recognition.py scripts/seal-reference-calibration.py; do
  if ! grep -Fq "$copied_cli" "${PROJECT_DIR}/Dockerfile"; then
    printf 'ERROR: application image does not copy reference CLI: %s\n' "$copied_cli" >&2
    failures=$((failures + 1))
  fi
done
if ! grep -Fqx 'transformers==4.57.3' "${PROJECT_DIR}/requirements-embedding.lock"; then
  printf '%s\n' 'ERROR: the Qwen3-VL-compatible Transformers version is not pinned.' >&2
  failures=$((failures + 1))
fi

if ((failures)); then
  printf 'Deployment checks failed: %s issue(s), %s shell script(s) inspected.\n' "$failures" "$checked" >&2
  exit 1
fi
printf 'Deployment checks passed: %s shell script(s); Compose checked when available.\n' "$checked"
