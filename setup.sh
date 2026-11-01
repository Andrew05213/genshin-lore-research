#!/usr/bin/env bash
# Apache-2.0. Only environment bootstrap; the existing builders qualify data.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "$(uname -s)" != Linux ]]; then
    echo 'Use setup.cmd on Windows. This entry supports Linux only.' >&2
    exit 1
fi
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    echo 'Usage: bash setup.sh [--jobs 1|2]'
    echo 'Prepare dependencies, build and verify all data/vectors, configure project Codex MCP.'
    exit 0
fi
mkdir -p data/setup-logs
log="data/setup-logs/linux-bootstrap-$(date -u +%Y%m%dT%H%M%S)-$$.log"
echo "Installer log: $log"

main() {
    local python='' candidate
    for candidate in python3.12 python3; do
        if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info[:2] != (3,12))'; then
            python="$candidate"
            break
        fi
    done
    if [[ -z "$python" ]] || ! command -v git >/dev/null || ! command -v g++ >/dev/null ||
        ! "$python" -c 'import venv, ensurepip, sysconfig, sys; from pathlib import Path; sys.exit(0 if (Path(sysconfig.get_path("include"))/"Python.h").is_file() else 1)' 2>/dev/null; then
        if ! command -v apt-get >/dev/null; then
            echo 'Install Git, a C++ compiler, Python 3.12 with venv/ensurepip and development headers, then rerun bash setup.sh.' >&2
            return 1
        fi
        local -a elevate=()
        if [[ "$EUID" -ne 0 ]]; then
            if ! command -v sudo >/dev/null; then
                echo 'Missing prerequisites: administrator installation via apt is required, but sudo is unavailable.' >&2
                return 1
            fi
            elevate=(sudo)
        fi
        "${elevate[@]}" apt-get update
        "${elevate[@]}" apt-get install -y git build-essential python3.12 python3.12-venv python3.12-dev
        python=python3.12
    fi
    export PYTHONUTF8=1
    "$python" scripts/setup_project.py "$@"
}

# Keep the actual installer exit code through tee, including dependency failures.
set +e
(
    set -e
    main "$@"
) 2>&1 | tee "$log"
codes=("${PIPESTATUS[@]}")
if [[ "${codes[0]}" -ne 0 ]]; then
    echo "Installation failed; existing data and logs preserved. See $log" >&2
    exit "${codes[0]}"
fi
exit "${codes[1]}"
