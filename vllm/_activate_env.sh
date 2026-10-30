#!/usr/bin/env bash
# Source this file, then call activate_vllm_environment gemma4 (or next).
activate_vllm_environment() {
    local env_name="$1"
    local conda_root="${VLLM_CONDA_ROOT:-/home2/sjyang114/miniconda3}"
    local cache_root="${VLLM_NODE_CACHE_ROOT:-/local-data/user-data/sjyang114/conda_cache}"
    local cache_prefix="$cache_root/$env_name"
    local selection

    if [[ -f "$cache_prefix/.node-cache-ready" &&
          -r "$cache_prefix/bin/activate" && -x "$cache_prefix/bin/python" &&
          -x "$cache_prefix/bin/vllm" ]]; then
        : "${CONDA_PREFIX:=}" "${PS1:=}"
        if ! source "$cache_prefix/bin/activate"; then
            printf 'Failed to activate local environment: %s\n' "$cache_prefix" >&2
            return 1
        fi
        if [[ "$(command -v python)" != "$cache_prefix/bin/python" ||
              "$(command -v vllm)" != "$cache_prefix/bin/vllm" ]]; then
            printf 'Local environment executable selection failed: %s\n' "$cache_prefix" >&2
            return 1
        fi
        selection=local
    else
        source "$conda_root/etc/profile.d/conda.sh" || return 1
        conda activate "$env_name" || return 1
        selection=original
    fi
    printf 'vLLM environment (%s): %s\n' "$selection" "$CONDA_PREFIX"
}
