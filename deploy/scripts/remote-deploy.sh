#!/usr/bin/env bash
# Runs ON the VPS, from the deploy directory (or any cwd: it resolves its own location).
#   remote-deploy.sh deploy <image>   pull + up -d + health check
#   remote-deploy.sh rollback         redeploy the previous image
# Idempotent: re-running with the same image is a no-op apart from `up -d` reconciling.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.deploy"
COMPOSE=(docker compose -f "$ROOT/docker-compose.prod.yml" --project-directory "$ROOT")
HEALTH_TRIES="${HEALTH_TRIES:-40}"

mkdir -p "$STATE"
[ -f "$ROOT/.env" ] || { echo "ERROR: $ROOT/.env not found" >&2; exit 1; }

wait_healthy() {
  local cid status
  for _ in $(seq 1 "$HEALTH_TRIES"); do
    cid="$("${COMPOSE[@]}" ps -q api || true)"
    if [ -n "$cid" ]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")"
      [ "$status" = "healthy" ] && return 0
    fi
    sleep 3
  done
  return 1
}

release() {
  local image="$1" current=""
  [ -f "$STATE/current_image" ] && current="$(cat "$STATE/current_image")"
  export API_IMAGE="$image"

  "${COMPOSE[@]}" pull
  "${COMPOSE[@]}" up -d --remove-orphans

  if wait_healthy; then
    # Keep the previous tag only when the image actually changed.
    if [ -n "$current" ] && [ "$current" != "$image" ]; then
      printf '%s\n' "$current" > "$STATE/previous_image"
    fi
    printf '%s\n' "$image" > "$STATE/current_image"
    echo "OK: $image is healthy."
  else
    echo "ERROR: api did not become healthy with $image." >&2
    "${COMPOSE[@]}" logs --tail=50 api >&2 || true
    if [ -n "$current" ] && [ "$current" != "$image" ]; then
      echo "Rollback hint: bash $ROOT/scripts/remote-deploy.sh deploy $current" >&2
    fi
    exit 1
  fi
}

case "${1:-}" in
  deploy)
    [ -n "${2:-}" ] || { echo "usage: $0 deploy <image>" >&2; exit 2; }
    release "$2"
    ;;
  rollback)
    [ -f "$STATE/previous_image" ] || { echo "ERROR: no previous image recorded" >&2; exit 1; }
    release "$(cat "$STATE/previous_image")"
    ;;
  *)
    echo "usage: $0 {deploy <image>|rollback}" >&2
    exit 2
    ;;
esac
