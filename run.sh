#!/bin/sh
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"
skill_dir=$(python3 -c 'import json; print(json.load(open("config.json"))["skill_dir"])')
exec timeout --signal=TERM --kill-after=10s 900s uv run --offline --project "$skill_dir" python sync.py "$@"
