#!/usr/bin/env bash
# Build and load app images for a local kind cluster (no PyPI inside Docker).
# Use when ``docker build -f deploy/Dockerfile`` fails with SSL errors inside the build VM.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CLUSTER_NAME="${KIND_CLUSTER_NAME:-school-analytics}"
APP_TAG="${APP_TAG:-local}"
OPERATOR_TAG="${OPERATOR_TAG:-local}"

if ! docker image inspect "school-analytics:${APP_TAG}" >/dev/null 2>&1; then
  echo "Missing image school-analytics:${APP_TAG}" >&2
  echo "Build the Python app image (must include uvicorn + alembic), for example:" >&2
  echo "  docker build -f deploy/Dockerfile -t school-analytics:${APP_TAG} ." >&2
  echo "If Docker PyPI TLS fails:" >&2
  echo "  source .venv/bin/activate && bash deploy/scripts/fetch_linux_arm64_wheels.sh" >&2
  echo "  docker build -f deploy/Dockerfile.kind -t school-analytics:${APP_TAG} ." >&2
  exit 1
fi

if command -v kind >/dev/null 2>&1; then
  kind load docker-image "school-analytics:${APP_TAG}" --name "$CLUSTER_NAME"
  if docker image inspect "school-analytics-operator:${OPERATOR_TAG}" >/dev/null 2>&1; then
    kind load docker-image "school-analytics-operator:${OPERATOR_TAG}" --name "$CLUSTER_NAME"
  fi
  echo "Loaded into kind cluster ${CLUSTER_NAME}. Verify:"
  echo "  docker exec ${CLUSTER_NAME}-control-plane crictl images | grep school-analytics"
else
  echo "kind not in PATH; add: eval \"\$(/opt/homebrew/bin/brew shellenv zsh)\"" >&2
  exit 1
fi
