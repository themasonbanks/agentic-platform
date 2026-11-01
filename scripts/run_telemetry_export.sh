#!/usr/bin/env bash
set -euo pipefail

deadline="${AGENTIC_EXPORT_TIMEOUT_SECONDS:-120}"
if [[ $# -ne 0 || ! "$deadline" =~ ^[1-9][0-9]{0,2}$ ]] || (( deadline > 240 )); then
  echo 'Invalid export deadline configuration' >&2
  exit 64
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
exec timeout --signal=TERM --kill-after=5s "${deadline}s" python3 -m telemetry.worker
