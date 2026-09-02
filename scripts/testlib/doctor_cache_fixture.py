#!/usr/bin/env python3
"""Build a deterministic, hardware-free shell fixture around doctor.sh."""

from __future__ import annotations

import shlex
import shutil
import stat
import sys
from pathlib import Path


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: doctor_cache_fixture.py TARGET DOCTOR_SCRIPT")

    target = Path(sys.argv[1])
    source_doctor = Path(sys.argv[2])
    real_repo = source_doctor.resolve().parent.parent
    platform_py = real_repo / "scripts" / "platform_reference.py"
    scripts = target / "scripts"
    bins = target / "bin"
    scripts.mkdir(parents=True)
    bins.mkdir()
    shutil.copy2(source_doctor, scripts / "doctor.sh")

    (scripts / "lib.sh").write_text(
        """#!/usr/bin/env bash
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HF_CACHE="${HF_CACHE:?fixture HF_CACHE required}"
_platform_shell=$(python3 PLATFORM_PY export-shell) || {
  printf '[%s] ERROR: platform reference is unavailable\\n' \\
    "${SCRIPT_NAME:-pulsar}" >&2
  return 1 2>/dev/null || exit 1
}
eval "$_platform_shell"
unset _platform_shell
MIN_OS_BUFFER_GIB="${MIN_OS_BUFFER_GIB:-$PULSAR_MIN_OS_BUFFER_GIB}"
HARD_FLOOR_AVAILABLE_GIB="${HARD_FLOOR_AVAILABLE_GIB:-$PULSAR_HARD_FLOOR_AVAILABLE_GIB}"
LAUNCH_SPIKE_GIB="${LAUNCH_SPIKE_GIB:-$PULSAR_LAUNCH_SPIKE_GIB}"
OVERHEAD_GIB_DEFAULT="${OVERHEAD_GIB_DEFAULT:-$PULSAR_OVERHEAD_GIB_DEFAULT}"
PULSAR_SSH=/bin/false
PULSAR_SSH_OPTS=()
CLUSTER_TOPOLOGY_COUNT="${PULSAR_TEST_TOPOLOGY_COUNT:-1}"
CLUSTER_TOPOLOGY_SSH_TRUSTED=0
print_hanging() { printf '%s%s\\n' "$1" "$2"; }
api_auth_curl_args() { local -n args_ref="$1"; args_ref=(); }
mem_available_gib_local() { printf '16\\n'; }
disk_free_gib() {
  df -BG "$1" | awk 'NR == 2 {gsub(/G/, "", $4); print $4}'
}
load_cluster_topology() { return 0; }
""".replace("PLATFORM_PY", shlex.quote(str(platform_py))),
        encoding="utf-8",
    )

    write_executable(
        scripts / "detect-fabric.sh",
        "#!/usr/bin/env bash\nprintf '%s\\n' "
        "'{\"topology\":{\"nodes\":[{}]}}'\n",
    )
    write_executable(
        scripts / "model-library.sh",
        "#!/usr/bin/env bash\nprintf '%s\\n' '{\"state\":\"not-configured\"}'\n",
    )
    (scripts / "model_library.py").write_text(
        "import sys\n"
        "if 'render-health' in sys.argv:\n"
        "    print('ok\\tmodel_library\\tmodel library not configured')\n",
        encoding="utf-8",
    )

    write_executable(
        bins / "uname",
        "#!/usr/bin/env bash\n[ \"${1:-}\" = -m ] && printf 'aarch64\\n'\n",
    )
    write_executable(
        bins / "nvidia-smi",
        "#!/usr/bin/env bash\nprintf 'NVIDIA GB10\\n'\n",
    )
    write_executable(
        bins / "docker",
        r'''#!/usr/bin/env bash
case "${1:-}" in
  info)
    if [ "${2:-}" = -f ]; then printf 'nvidia\n'; else printf 'Runtimes: nvidia\n'; fi
    ;;
  ps) exit 0 ;;
  *) exit 1 ;;
esac
''',
    )
    write_executable(bins / "ss", "#!/usr/bin/env bash\nexit 0\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
