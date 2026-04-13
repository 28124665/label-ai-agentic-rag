#!/usr/bin/env bash
# 容器入口：可选启动 Promtail 采集应用与 Nginx 日志，再 exec 原 entrypoint.sh（Helm 的 args 会传入）。
# CONFIG_ENV=prod 时使用 promtail-config-prod.yaml，否则使用 boe；若集群侧已有 Promtail sidecar，可设 ENABLE_PROMTAIL=false。

set -e

CONFIG_DIR="/data-knowledge-api/config"
ENABLE_PROMTAIL="${ENABLE_PROMTAIL:-true}"
PROMTAIL_ENV="${CONFIG_ENV:-boe}"
if [[ "$PROMTAIL_ENV" == "prod" ]]; then
  PROMTAIL_CFG="$CONFIG_DIR/promtail-config-prod.yaml"
else
  PROMTAIL_CFG="$CONFIG_DIR/promtail-config-boe.yaml"
fi

if [[ "$ENABLE_PROMTAIL" != "true" && "$ENABLE_PROMTAIL" != "1" ]]; then
  echo "ENABLE_PROMTAIL=$ENABLE_PROMTAIL，跳过 Promtail"
elif command -v promtail >/dev/null 2>&1 && [[ -f "$PROMTAIL_CFG" ]]; then
  echo "Starting Promtail... (config=$PROMTAIL_CFG)"
  promtail -config.file="$PROMTAIL_CFG" &
else
  echo "Skipping Promtail (no binary or missing $PROMTAIL_CFG)"
fi

exec /data-knowledge-api/entrypoint.sh "$@"
