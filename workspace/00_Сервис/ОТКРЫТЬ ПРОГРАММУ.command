#!/bin/zsh
TASK_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$TASK_ROOT" || exit 1
exec python3 "$TASK_ROOT/00_Сервис/analyst_folder.py"
