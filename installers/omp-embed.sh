#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# One embedding server for every omp session on this machine.
#
# omp's memory backend (mnemopi) embeds text with a local ONNX model, and it
# does so in a `__omp_worker_mnemopi_embed` subprocess per omp process: each one
# loads the same model and holds ~1 GB of anonymous memory for the life of the
# session, so a devbox with 30 sessions spends ~30 GB on 30 copies of one model.
# Upstream is deliberating an idle-unload policy (can1357/oh-my-pi#9908, #10043);
# until that lands, mnemopi's existing OpenAI-compatible embedding endpoint is
# pointed at one shared server instead, and no in-process worker is spawned.
#
# The server is Hugging Face text-embeddings-inference serving the exact model
# the banks already store (BAAI/bge-base-en-v1.5, 768 dims). Its vectors match
# omp's fastembed output to cosine 0.99999 (measured 2026-09-12), so stored
# vectors stay valid and no bank is rebuilt. Docker's own restart policy keeps
# it up across daemon restarts and reboots.
#
# Machine-local role installer, like forward.sh: not sourced by install.sh.
# shims/omp exports MNEMOPI_EMBEDDING_API_URL from the endpoint file written
# below whenever the server answers, so sessions on machines without it keep
# omp's in-process worker.

IMAGE="ghcr.io/huggingface/text-embeddings-inference:cpu-1.9"
MODEL="BAAI/bge-base-en-v1.5"
NAME="omp-embed"
PORT="${OMP_EMBED_PORT:-8087}"
ENDPOINT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omp-embed"
ENDPOINT_FILE="${ENDPOINT_DIR}/endpoint"

command -v docker >/dev/null || { echo "omp-embed: docker is required (installers/docker.sh)" >&2; exit 1; }

docker pull "$IMAGE" >/dev/null

current_image="$(docker inspect --format '{{.Config.Image}}' "$NAME" 2>/dev/null || true)"
if [ "$current_image" = "$IMAGE" ]; then
    docker start "$NAME" >/dev/null
else
    [ -n "$current_image" ] && docker rm -f "$NAME" >/dev/null
    # --auto-truncate: mnemopi caps input at 8192 chars, well past this model's
    # 512 tokens; fastembed truncates silently, so the server must too.
    # --max-client-batch-size 256: mnemopi re-embeds a bank in batches of 128.
    docker run -d --name "$NAME" --restart unless-stopped \
        -p "127.0.0.1:${PORT}:80" \
        -v omp-embed-hf:/data \
        "$IMAGE" --model-id "$MODEL" --auto-truncate --max-client-batch-size 256 --payload-limit 8000000 >/dev/null
fi

# First start downloads the model (~440 MB); wait for the router's ready signal.
for _ in $(seq 1 120); do
    if curl -fsS -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -fsS -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null || {
    echo "omp-embed: server did not become healthy; docker logs ${NAME}" >&2
    exit 1
}

mkdir -p "$ENDPOINT_DIR"
printf 'http://127.0.0.1:%s/v1\n' "$PORT" > "$ENDPOINT_FILE"
echo "omp-embed: ${MODEL} serving at $(cat "$ENDPOINT_FILE") (endpoint file ${ENDPOINT_FILE}); new omp sessions use it via shims/omp."
