#!/usr/bin/env bash
# Experiment-only resource monitoring for supervised model onboarding.
# This command is intentionally not called by pulsar/up/serve/catalog paths.
set -euo pipefail
# Used by sourced lib.sh diagnostics.
# shellcheck disable=SC2034
SCRIPT_NAME=model-serving-experiment-monitor

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PY_TOOL="${PULSAR_MODEL_SERVING_EXPERIMENT_MONITOR_PY:-$SCRIPT_DIR/model_serving_experiment_monitor.py}"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib.sh"
cd "$REPO_DIR"

usage() {
  cat <<'EOF'
usage:
  scripts/model-serving-experiment-monitor.sh start PROFILE --state-dir DIR [--node NODE] [--interval SECONDS]
  scripts/model-serving-experiment-monitor.sh summarize --state-dir DIR --started-at UTC --ended-at UTC --qualification-scope SCOPE --result-json FILE
  scripts/model-serving-experiment-monitor.sh stop --state-dir DIR

This is an experiment-only onboarding diagnostic. It never starts or stops a
model and is not part of ordinary catalog serving.
EOF
}

[ -f "$PY_TOOL" ] || die "missing experiment monitor Python tool"
command_name="${1:-}"
[ -n "$command_name" ] || { usage >&2; exit 2; }
shift

state_dir=""
profile=""
node_selector=""
interval="1"
started_at=""
ended_at=""
qualification_scope=""
result_json=""

case "$command_name" in
  start)
    profile="${1:-}"
    [ -n "$profile" ] || die "start requires PROFILE" 2
    shift
    ;;
  summarize|stop) ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *) die "unknown model-serving-experiment-monitor command: $command_name" 2 ;;
esac

while [ "$#" -gt 0 ]; do
  case "$1" in
    --state-dir)
      [ "$#" -ge 2 ] || die "--state-dir requires a value" 2
      state_dir="$2"
      shift
      ;;
    --node)
      [ "$#" -ge 2 ] || die "--node requires a value" 2
      node_selector="$2"
      shift
      ;;
    --interval)
      [ "$#" -ge 2 ] || die "--interval requires a value" 2
      interval="$2"
      shift
      ;;
    --started-at)
      [ "$#" -ge 2 ] || die "--started-at requires a value" 2
      started_at="$2"
      shift
      ;;
    --ended-at)
      [ "$#" -ge 2 ] || die "--ended-at requires a value" 2
      ended_at="$2"
      shift
      ;;
    --qualification-scope)
      [ "$#" -ge 2 ] || die "--qualification-scope requires a value" 2
      qualification_scope="$2"
      shift
      ;;
    --result-json)
      [ "$#" -ge 2 ] || die "--result-json requires a value" 2
      result_json="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1" 2 ;;
  esac
  shift
done

[ -n "$state_dir" ] || die "--state-dir is required" 2

if [ "$command_name" = summarize ]; then
  if [ -z "$started_at" ] || [ -z "$ended_at" ] \
      || [ -z "$qualification_scope" ] || [ -z "$result_json" ]; then
    die "summarize requires --started-at, --ended-at, --qualification-scope, and --result-json" 2
  fi
  exec python3 "$PY_TOOL" summarize \
    --repo-root "$REPO_DIR" \
    --state-dir "$state_dir" \
    --started-at "$started_at" \
    --ended-at "$ended_at" \
    --qualification-scope "$qualification_scope" \
    --result-json "$result_json"
fi

if [ "$command_name" = stop ]; then
  python3 "$PY_TOOL" check-session \
    --repo-root "$REPO_DIR" --state-dir "$state_dir"
  state_abs="$(cd "$state_dir" && pwd -P)"
  jobs_file="$state_abs/jobs.tsv"
  session_token="$(python3 "$PY_TOOL" session-token \
    --repo-root "$REPO_DIR" --state-dir "$state_dir")"
  owned_monitor_pid() {
    local pid="$1" cmdline
    [ -r "/proc/$pid/cmdline" ] || return 1
    cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null)" || return 1
    [[ "$cmdline" == *"$session_token"* && "$cmdline" == *" collect "* ]]
  }
  if [ -f "$jobs_file" ]; then
    [ ! -L "$jobs_file" ] || die "monitor jobs file must not be a symlink"
    [ "$(stat -c '%a' "$jobs_file" 2>/dev/null)" = 600 ] \
      || die "monitor jobs file must have mode 0600"
    while IFS=$'\t' read -r _rank pid; do
      [[ "$pid" =~ ^[1-9][0-9]*$ ]] || continue
      if kill -0 "$pid" 2>/dev/null; then
        owned_monitor_pid "$pid" \
          || die "refusing to stop a process not owned by this monitor session"
        kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
      fi
    done <"$jobs_file"
    for _attempt in $(seq 1 50); do
      alive=0
      while IFS=$'\t' read -r _rank pid; do
        [[ "$pid" =~ ^[1-9][0-9]*$ ]] || continue
        kill -0 "$pid" 2>/dev/null && alive=1
      done <"$jobs_file"
      [ "$alive" = 0 ] && break
      sleep 0.1
    done
    while IFS=$'\t' read -r _rank pid; do
      [[ "$pid" =~ ^[1-9][0-9]*$ ]] || continue
      if kill -0 "$pid" 2>/dev/null; then
        owned_monitor_pid "$pid" \
          || die "refusing to force-stop a process not owned by this monitor session"
        kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
      fi
    done <"$jobs_file"
  fi
  python3 "$PY_TOOL" stop-session \
    --repo-root "$REPO_DIR" --state-dir "$state_dir"
  echo "experiment resource monitor stopped; private samples remain in $state_dir"
  exit 0
fi

load_conf "$profile"
container_name="$(container_name_for "$profile" "$NODES")"
declare -a rank_labels=()
declare -a rank_hosts=()
declare -a rank_local=()

if [ "$NODES" -eq 1 ]; then
  resolve_single_node_placement "$node_selector" \
    || die "cannot resolve physical node placement '$node_selector'"
  rank_labels=(single)
  if [ "$SINGLE_NODE_REMOTE" = 1 ]; then
    rank_hosts=("$SINGLE_NODE_SSH_HOST")
    rank_local=(0)
  else
    rank_hosts=(local)
    rank_local=(1)
  fi
elif [ -n "$node_selector" ]; then
  die "--node is only valid for one-node profiles" 2
else
  require_cluster_nodes "$NODES" \
    || die "confirmed topology has fewer than $NODES required ranks"
  for ((rank = 0; rank < NODES; rank++)); do
    rank_labels+=("$rank")
    if [ "$rank" -eq 0 ]; then
      rank_hosts+=(local)
      rank_local+=(1)
    else
      rank_hosts+=("${CLUSTER_NODE_SSH_HOSTS[$rank]}")
      rank_local+=(0)
    fi
  done
fi

declare -a init_args=(
  init-session
  --repo-root "$REPO_DIR"
  --state-dir "$state_dir"
  --profile "$profile"
  --interval "$interval"
)
command -v setsid >/dev/null 2>&1 \
  || die "setsid is required for experiment monitoring"
for rank_label in "${rank_labels[@]}"; do
  init_args+=(--rank "${rank_label}=rank-${rank_label}.jsonl")
done
session_token="$(python3 "$PY_TOOL" "${init_args[@]}")"
state_abs="$(cd "$state_dir" && pwd -P)"
jobs_file="$state_abs/jobs.tsv"
: >"$jobs_file"
chmod 600 "$jobs_file"

started=0
cleanup_failed_start() {
  local index pid
  if [ "$started" -eq 1 ] && [ -f "$jobs_file" ]; then
    while IFS=$'\t' read -r _rank pid; do
      [[ "$pid" =~ ^[1-9][0-9]*$ ]] || continue
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    done <"$jobs_file"
  fi
}
trap cleanup_failed_start ERR

for index in "${!rank_labels[@]}"; do
  rank_label="${rank_labels[$index]}"
  raw_file="$state_abs/rank-${rank_label}.jsonl"
  error_file="$state_abs/rank-${rank_label}.stderr.log"
  : >"$raw_file"
  : >"$error_file"
  chmod 600 "$raw_file" "$error_file"
  if [ "${rank_local[$index]}" = 1 ]; then
    setsid python3 -u "$PY_TOOL" collect \
      --rank-label "$rank_label" \
      --container-name "$container_name" \
      --interval "$interval" \
      --session-token "$session_token" \
      </dev/null >"$raw_file" 2>"$error_file" &
  else
    host="${rank_hosts[$index]}"
    setsid "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
      python3 -u - collect \
      --rank-label "$rank_label" \
      --container-name "$container_name" \
      --interval "$interval" \
      --session-token "$session_token" \
      <"$PY_TOOL" >"$raw_file" 2>"$error_file" &
  fi
  pid=$!
  printf '%s\t%s\n' "$rank_label" "$pid" >>"$jobs_file"
  started=1
done
trap - ERR

echo "experiment resource monitor started"
echo "  profile: $profile"
echo "  ranks:   ${rank_labels[*]}"
echo "  cadence: ${interval}s"
echo "  state:   $state_dir"
echo "This monitor is onboarding-only and is not part of catalog serving."
