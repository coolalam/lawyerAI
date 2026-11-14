#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo '请先按 README 安装 Python 依赖。'
  exit 1
fi
if [ ! -f dist/index.html ]; then
  echo '请先运行 npm ci && npm run build。'
  exit 1
fi
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port "${LAW_PORT:-8000}"
