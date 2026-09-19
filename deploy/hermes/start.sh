#!/bin/sh
set -eu

cp /app/deploy/hermes/config.yaml /app/hermes-home/config.yaml

if [ -n "${GITHUB_MCP_TOKEN:-}" ]; then
  cat /app/deploy/hermes/github-live.yaml.fragment >> /app/hermes-home/config.yaml
  echo "ContextPack: live GitHub MCP enabled"
else
  echo "ContextPack: GITHUB_MCP_TOKEN not set; live GitHub MCP disabled"
fi

exec python -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8080}"
