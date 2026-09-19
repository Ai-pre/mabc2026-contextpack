#!/bin/sh
set -eu

cp /app/deploy/hermes/config.yaml /app/hermes-home/config.yaml

if [ -n "${GITHUB_MCP_TOKEN:-}" ]; then
  echo "ContextPack: live GitHub connector enabled"
else
  echo "ContextPack: GITHUB_MCP_TOKEN not set; live GitHub connector disabled"
fi

if [ "${GITHUB_REMOTE_MCP_ENABLED:-0}" = "1" ] && [ -n "${GITHUB_MCP_TOKEN:-}" ]; then
  cat /app/deploy/hermes/github-live.yaml.fragment >> /app/hermes-home/config.yaml
  echo "ContextPack: official GitHub remote MCP detail fallback enabled"
fi

if [ -n "${SLACK_BOT_TOKEN:-}" ]; then
  echo "ContextPack: live Slack connector enabled"
else
  echo "ContextPack: SLACK_BOT_TOKEN not set; live Slack connector disabled"
fi

if [ -n "${NOTION_API_KEY:-}" ]; then
  echo "ContextPack: live Notion connector enabled"
else
  echo "ContextPack: NOTION_API_KEY not set; live Notion connector disabled"
fi

exec python -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8080}"
