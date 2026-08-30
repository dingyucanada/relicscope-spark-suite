#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

service_key="$(cat /run/secrets/service_api_key)"
if [[ "${#service_key}" -lt 32 ]]; then
  printf '%s\n' 'FATAL: service API key is missing or shorter than 32 characters' >&2
  exit 78
fi
export VLLM_API_KEY="$service_key"
unset service_key

common=(
  serve "$VISION_MODEL_SOURCE"
  --served-model-name "$VISION_MODEL"
  --revision "$VISION_MODEL_REVISION"
  --tokenizer-revision "$VISION_MODEL_REVISION"
  --host 0.0.0.0
  --port 8000
  --max-model-len "$VISION_MAX_MODEL_LEN"
  --gpu-memory-utilization "$VISION_GPU_MEMORY_UTILIZATION"
  --max-num-seqs "$MODEL_MAX_CONCURRENCY"
  --limit-mm-per-prompt '{"image":1,"video":1,"audio":0}'
  --enable-prefix-caching
)

case "$MODEL_PROFILE" in
  qwen3-vl)
    exec vllm "${common[@]}"
    ;;
  nemotron-omni)
    exec vllm "${common[@]}" \
      --trust-remote-code \
      --code-revision "$VISION_MODEL_REVISION" \
      --max-num-batched-tokens 32768 \
      --video-pruning-rate 0.5 \
      --media-io-kwargs '{"video":{"fps":2,"num_frames":256}}' \
      --reasoning-parser nemotron_v3 \
      --enable-auto-tool-choice \
      --tool-call-parser qwen3_coder
    ;;
  *)
    printf 'FATAL: unsupported MODEL_PROFILE=%s\n' "$MODEL_PROFILE" >&2
    exit 64
    ;;
esac
