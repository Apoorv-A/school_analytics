#!/usr/bin/env bash
# Populate deploy/docker-wheels-linux for deploy/Dockerfile.kind (Linux arm64, Python 3.12).
# Uses the host pip index (corp mirror or PyPI). Run from repo root with .venv activated.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${ROOT}/deploy/docker-wheels-linux"
mkdir -p "$DEST"

if ! command -v pip >/dev/null 2>&1; then
  echo "Activate .venv or ensure pip is on PATH" >&2
  exit 1
fi

echo "Downloading pure-Python wheels from requirements.lock..."
pip download -r "${ROOT}/requirements.lock" -d "$DEST" \
  --only-binary=:all: \
  --platform any 2>/dev/null || {
  # pip has no --platform any; copy local py3-none-any wheels instead.
  find "${ROOT}/deploy/docker-wheels" -name '*-py3-none-any.whl' -exec cp -n {} "$DEST/" \; 2>/dev/null || true
}

pip_download_linux() {
  local spec="$1"
  for plat in manylinux_2_28_aarch64 manylinux2014_aarch64; do
    if pip download "$spec" -d "$DEST" \
      --only-binary=:all: \
      --platform "$plat" \
      --python-version 312 \
      --abi cp312 2>/dev/null; then
      return 0
    fi
  done
  echo "WARN: no linux arm64 wheel for $spec" >&2
  return 1
}

BINARY_SPECS=(
  greenlet==3.5.6
  pydantic_core==2.46.5
  cffi==2.1.1
  httptools==0.8.0
  markupsafe==3.0.3
  pyyaml==6.0.3
  rpds_py==2026.9.1
  sqlalchemy==2.0.53
  uvloop==0.22.1
  watchfiles==1.2.0
  websockets==17.1
  bcrypt==5.0.0
  cryptography==50.0.2
  pynacl==1.6.2
)

for spec in "${BINARY_SPECS[@]}"; do
  pip_download_linux "$spec" || true
done

pip_download_linux 'psycopg[binary]==3.2.9' || pip_download_linux psycopg==3.2.9 || true

# Ensure pure-python lockfile packages exist (skip already present).
pip download -r "${ROOT}/requirements.lock" -d "$DEST" 2>/dev/null || true

echo "Wheels in ${DEST}: $(ls -1 "$DEST" | wc -l | tr -d ' ')"
