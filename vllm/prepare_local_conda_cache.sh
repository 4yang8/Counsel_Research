#!/usr/bin/env bash
# Run inside a GPU allocation with an archive made by conda-pack --dest-prefix.
# Usage: bash prepare_local_conda_cache.sh next /shared/path/next.local-v1.tar
set -euo pipefail
umask 077

env_name="${1:?Usage: $0 ENV_NAME CONDA_PACK_ARCHIVE}"
archive="${2:?Usage: $0 ENV_NAME CONDA_PACK_ARCHIVE}"
[[ "$env_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo 'Invalid environment name.' >&2
  exit 1
}
[[ -d /local-data && -r "$archive" ]] || {
  echo 'Run on a compute node with /local-data and a readable archive.' >&2
  exit 1
}

cache_root="/local-data/user-data/$(id -un)/conda_cache"
destination="${cache_root}/${env_name}"
mkdir -p -- "$cache_root"
exec 9>"${cache_root}/.${env_name}.prepare.lock"
flock 9
receipt="$(realpath -- "$archive") $(stat -c '%s:%Y' -- "$archive")"
if [[ -f "$destination/.local-cache-ready" ]] &&
   [[ "$(cat "$destination/.local-cache-ready")" == "$receipt" ]] &&
   [[ -x "$destination/bin/python" ]]; then
  printf '%s: reusing %s\n' "$(hostname)" "$destination"
  exit 0
fi
if [[ -e "$destination" ]]; then
  printf 'Refusing to replace an existing, different or unverified cache: %s\n' "$destination" >&2
  exit 1
fi

staging="$(mktemp -d "${cache_root}/.${env_name}.prepare.XXXXXX")"
trap 'if [[ -n "$staging" && -d "$staging" ]]; then rm -rf -- "$staging"; fi' EXIT
printf '%s: extracting %s\n' "$(hostname)" "$archive"
tar --extract --file="$archive" --directory="$staging" --no-same-owner
mv -T -- "$staging" "$destination"
staging=''

# Validate the relocated interpreter and libraries without loading a model.
printf '%s: validating %s\n' "$(hostname)" "$destination"
env -u PYTHONHOME -u PYTHONPATH OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  timeout -k 10s 180s "$destination/bin/python" -u - "$destination" <<'PY'
import os
import sys
import time

prefix = os.path.realpath(sys.argv[1])
assert os.path.realpath(sys.prefix) == prefix, (sys.prefix, prefix)
started = time.monotonic()
import torch
torch_seconds = time.monotonic() - started
started = time.monotonic()
import vllm
vllm_seconds = time.monotonic() - started
for module in (torch, vllm):
    assert os.path.realpath(module.__file__).startswith(prefix + os.sep), module.__file__
print(f'Python: {sys.executable}')
print(f'torch {torch.__version__}: {torch_seconds:.2f}s')
print(f'vllm {vllm.__version__}: {vllm_seconds:.2f}s')
PY
printf '%s\n' "$receipt" > "$destination/.local-cache-ready"
printf '%s: READY %s\n' "$(hostname)" "$destination"
