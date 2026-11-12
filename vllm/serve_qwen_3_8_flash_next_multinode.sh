#!/usr/bin/env bash
# Reuse the single-node Qwen recipe inside an existing uniform Slurm allocation.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
script_path="$script_dir/$(basename -- "${BASH_SOURCE[0]}")"
base_script="$script_dir/serve_qwen_3_8_flash_next_singlenode.sh"

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: bash vllm/serve_qwen_3_8_flash_next_multinode.sh [additional vLLM options]

First allocate equal GPU counts on every node, for example: dpro 1 8 2
Run this script from either the login host or the first allocated GPU node.
It uses every allocated GPU with TP = GPUs per node * node count, PP = 1,
and the existing Qwen FP8 script's expert parallelism and CPU embedding offload.
Model-specific TP constraints still apply to the total GPU count.

Defaults: PORT=8002, MAX_MODEL_LEN=32768, MAX_NUM_SEQS=64,
MAX_NUM_BATCHED_TOKENS=1024, GPU_MEMORY_UTILIZATION=0.95, MOE_BACKEND=auto.
Set these environment variables before launching to change the defaults.
VLLM_ENFORCE_EAGER=0 enables the base recipe's normal compilation behavior.
VLLM_HF_HOME defaults to ~/.cache/huggingface; HF_HUB_OFFLINE defaults to 1.
VLLM_MASTER_PORT overrides the job-derived rendezvous port.
VLLM_NODE_CACHE_DIR overrides the node-local cache root (default: /tmp/.../job-ID).
VLLM_MULTINODE_LOG_DIR overrides the run's shared log directory.

Ctrl-C stops the server. Exit the allocation shell to return the GPUs.
EOF
}

resolve_ip() {
  local address
  address="$(getent ahostsv4 "$1" | awk 'NR == 1 {print $1}')" || return 1
  [[ -n "$address" ]] || return 1
  printf '%s\n' "$address"
}

run_worker() {
  local rank="${SLURM_PROCID:?Missing Slurm worker rank}"
  local node node_ip interface cache_root
  [[ "$rank" =~ ^[0-9]+$ && "$rank" -lt "$VLLM_SLURM_NNODES" ]] || die "Invalid worker rank: $rank"
  node="$(hostname -s)"
  node_ip="$(resolve_ip "$node")" || die "Cannot resolve GPU node: $node"
  export VLLM_HOST_IP="$node_ip"
  interface="$(ip -o -4 addr show | awk -v addr="$node_ip" '{split($4, ip, "/"); if (ip[1] == addr) print $2}')"
  if [[ -n "$interface" ]]; then
    export NCCL_SOCKET_IFNAME="${NCCL_SOCKET_IFNAME:-$interface}"
    export GLOO_SOCKET_IFNAME="${GLOO_SOCKET_IFNAME:-$interface}"
  fi
  export NCCL_IB_DISABLE="${NCCL_IB_DISABLE:-1}"
  export VLLM_WORKER_MULTIPROC_METHOD=spawn
  export VLLM_USE_RUST_FRONTEND=0
  export OMP_NUM_THREADS="${OMP_NUM_THREADS:-$((VLLM_SLURM_CPUS < 4 ? VLLM_SLURM_CPUS : 4))}"
  export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"

  # Keep JIT products off NFS; both nodes compiling the same shared cache failed.
  cache_root="${VLLM_NODE_CACHE_DIR:-/tmp/vllm-$(id -un)/next-job-$SLURM_JOB_ID}"
  [[ "$cache_root" == /* ]] || die 'VLLM_NODE_CACHE_DIR must be an absolute node-local path'
  export TRITON_CACHE_DIR="$cache_root/triton"
  export VLLM_CACHE_ROOT="$cache_root/vllm"
  export DG_JIT_CACHE_DIR="$cache_root/deep_gemm"
  export TORCH_EXTENSIONS_DIR="$cache_root/torch_extensions"
  export FLASHINFER_WORKSPACE_BASE="$cache_root/flashinfer"
  mkdir -p -- "$TRITON_CACHE_DIR" "$VLLM_CACHE_ROOT" "$DG_JIT_CACHE_DIR" \
    "$TORCH_EXTENSIONS_DIR" "$FLASHINFER_WORKSPACE_BASE"
  export LOG_DIR="$VLLM_SLURM_LOG_DIR/node-$rank"

  local -a visible_devices=() extra_args=()
  [[ -n "${CUDA_VISIBLE_DEVICES:-}" && "$CUDA_VISIBLE_DEVICES" != -1 ]] || die "No assigned GPUs on $node"
  IFS=',' read -r -a visible_devices <<< "$CUDA_VISIBLE_DEVICES"
  [[ ${#visible_devices[@]} -eq "$VLLM_SLURM_GPUS_PER_NODE" ]] || die "Unexpected GPU count on $node: $CUDA_VISIBLE_DEVICES"
  if (( rank != 0 )); then extra_args+=(--headless); fi
  if [[ ${VLLM_ENFORCE_EAGER:-1} == 1 ]]; then extra_args+=(--enforce-eager); fi
  printf 'Job=%s node=%s rank=%s/%s GPUs=%s TP=%s cache=%s\n' \
    "$SLURM_JOB_ID" "$node" "$rank" "$VLLM_SLURM_NNODES" \
    "$CUDA_VISIBLE_DEVICES" "$TENSOR_PARALLEL_SIZE" "$cache_root"
  exec bash "$base_script" \
    --distributed-executor-backend mp --nnodes "$VLLM_SLURM_NNODES" \
    --node-rank "$rank" --master-addr "$VLLM_SLURM_MASTER_ADDR" \
    --master-port "$VLLM_SLURM_MASTER_PORT" --pipeline-parallel-size 1 \
    --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS" "${extra_args[@]}" "$@"
}

if [[ ${1:-} == --slurm-worker ]]; then
  shift
  run_worker "$@"
fi
if [[ ${1:-} == --help || ${1:-} == -h ]]; then usage; exit 0; fi
[[ -n ${SLURM_JOB_ID:-} ]] || die 'First allocate GPU nodes with a d command, for example: dpro 1 8 2'
[[ "$SLURM_JOB_ID" =~ ^[1-9][0-9]*$ ]] || die 'Invalid SLURM_JOB_ID'
[[ -r "$base_script" ]] || die "Base Qwen script not found: $base_script"

# Topology belongs to this launcher, rather than user-supplied vLLM arguments.
for argument in "$@"; do
  case "$argument" in
    --tensor-parallel-size|--tensor-parallel-size=*|-tp|--pipeline-parallel-size|--pipeline-parallel-size=*|-pp|\
    --nnodes|--nnodes=*|-n|--node-rank|--node-rank=*|-r|--headless|\
    --master-addr|--master-addr=*|--master-port|--master-port=*|\
    --distributed-executor-backend|--distributed-executor-backend=*|\
    --data-parallel-size|--data-parallel-size=*|-dp)
      die "Topology is derived from the Slurm allocation; do not pass $argument" ;;
  esac
done

job_info="$(scontrol show job -o "$SLURM_JOB_ID")" || die 'Cannot read the Slurm allocation'
[[ "$job_info" =~ (^|[[:space:]])JobState=RUNNING([[:space:]]|$) ]] || die 'The Slurm allocation must be RUNNING'
[[ "$job_info" =~ (^|[[:space:]])NodeList=([^[:space:]]+) ]] || die 'Missing allocation node list'
node_list="${BASH_REMATCH[2]}"
node_names="$(scontrol show hostnames "$node_list")" || die 'Cannot expand the allocation node list'
[[ -n "$node_names" ]] || die 'The allocation contains no nodes'
mapfile -t nodes <<< "$node_names"
[[ "$job_info" =~ (^|[[:space:]])CPUs/Task=([1-9][0-9]*) ]] || die 'Missing CPUs per task; allocate with a d command'
cpus_per_node="${BASH_REMATCH[2]}"
[[ "$job_info" =~ (^|[[:space:]])TresPerNode=([^[:space:]]+) ]] || die 'Uniform GPUs per node are required; allocate with a d command'
tres_per_node="${BASH_REMATCH[2]}"
gpus_per_node=0
IFS=',' read -r -a resources <<< "$tres_per_node"
for resource in "${resources[@]}"; do
  [[ "$resource" == gres/gpu:* ]] || continue
  count="${resource##*:}"
  [[ "$count" =~ ^[1-9][0-9]*$ ]] || die "Invalid GPU resource: $resource"
  gpus_per_node=$((gpus_per_node + count))
done
(( gpus_per_node > 0 )) || die 'Uniform GPUs per node are required; allocate with a d command'
total_gpus=$((gpus_per_node * ${#nodes[@]}))
[[ ${TENSOR_PARALLEL_SIZE:-$total_gpus} == "$total_gpus" ]] || die "TENSOR_PARALLEL_SIZE must equal the allocated GPU count ($total_gpus)"
export TENSOR_PARALLEL_SIZE="$total_gpus"
export VLLM_SLURM_NNODES="${#nodes[@]}"
export VLLM_SLURM_GPUS_PER_NODE="$gpus_per_node"
export VLLM_SLURM_CPUS="$cpus_per_node"
master_addr="$(resolve_ip "${nodes[0]}")" || die "Cannot resolve head node: ${nodes[0]}"
export VLLM_SLURM_MASTER_ADDR="$master_addr"
export VLLM_SLURM_MASTER_PORT="${VLLM_MASTER_PORT:-$((20000 + SLURM_JOB_ID % 20000))}"
export PORT="${PORT:-8002}"
for port in "$PORT" "$VLLM_SLURM_MASTER_PORT"; do
  [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) || die "Invalid port: $port"
done
[[ "$PORT" != "$VLLM_SLURM_MASTER_PORT" ]] || die 'API and rendezvous ports must be different'
export HF_HOME="${VLLM_HF_HOME:-$HOME/.cache/huggingface}"
export HF_TOKEN_PATH="${HF_TOKEN_PATH:-$HOME/.huggingface/token}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
export MAX_NUM_SEQS="${MAX_NUM_SEQS:-64}"
export MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-1024}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.95}"
export MOE_BACKEND="${MOE_BACKEND:-auto}"
project_dir="$(cd -- "$script_dir/.." && pwd)"
export VLLM_SLURM_LOG_DIR="${VLLM_MULTINODE_LOG_DIR:-$project_dir/logs/qwen3_8_flash_next_multinode/$(date +%Y%m%d_%H%M%S)_job${SLURM_JOB_ID}_$$}"
mkdir -p -- "$VLLM_SLURM_LOG_DIR"
printf 'Allocation: %s\nNodes: %s\nGPUs per node: %s\nTotal GPUs / TP: %s\nAPI: http://%s:%s/v1\nLogs: %s\n' \
  "$SLURM_JOB_ID" "$node_list" "$gpus_per_node" "$total_gpus" \
  "${nodes[0]}" "$PORT" "$VLLM_SLURM_LOG_DIR"

# --overlap also works when called from the first-node shell opened by DGPU.
srun --jobid="$SLURM_JOB_ID" --nodes="${#nodes[@]}" --ntasks="${#nodes[@]}" \
  --ntasks-per-node=1 --cpus-per-task="$cpus_per_node" --gres="gpu:$gpus_per_node" \
  --exact --overlap --cpu-bind=none --kill-on-bad-exit=1 --export=ALL --unbuffered --label \
  bash "$script_path" --slurm-worker "$@" 2>&1 | tee "$VLLM_SLURM_LOG_DIR/launcher.log"
