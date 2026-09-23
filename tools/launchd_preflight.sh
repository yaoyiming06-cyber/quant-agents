#!/bin/zsh
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

SYSTEM_PYTHON=""
for candidate in \
  /opt/homebrew/opt/python@3.14/bin/python3.14 \
  /opt/homebrew/bin/python3 \
  /usr/bin/python3; do
  if [[ -x "$candidate" ]]; then
    SYSTEM_PYTHON="$candidate"
    break
  fi
done

if [[ -x /usr/bin/brctl ]]; then
  /usr/bin/brctl download \
    "$ROOT/tools/runtime_maintenance.py" \
    "$ROOT/tools/serve_dashboard.py" \
    "$ROOT/tools/run_preopen_check.py" \
    "$ROOT/tools/run_intraday_update.py" \
    "$ROOT/tools/run_post_close_update.py" \
    "$ROOT/tools/build_dashboard_data.py" \
    "$ROOT/web_dashboard/app.js" \
    "$ROOT/web_dashboard/index.html" \
    "$ROOT/web_dashboard/data.js" \
    "$ROOT/web_dashboard/styles.css" >/dev/null 2>&1 || true
fi

if [[ -n "$SYSTEM_PYTHON" ]]; then
  "$SYSTEM_PYTHON" "$ROOT/tools/runtime_maintenance.py" --quick --quiet >/dev/null 2>&1 || true
fi

if [[ $# -eq 0 ]]; then
  exit 0
fi

exec "$@"
