#!/usr/bin/env bash
# Backend pipeline. Usage: ./pipelines/backend/ci.sh <test|build|scan|smoke|push|all>
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

COMPONENT=backend

cmd_test() {
  # MUST run from backend/ -- pyproject.toml sets asyncio_mode="auto" there, so
  # from the repo root every async test fails
  log "pytest"
  cd "$REPO_ROOT/backend"
  python -m pip install --quiet -e ".[dev]"
  python -m pytest -q
}

cmd_build() { build_image "$COMPONENT"; }
cmd_scan()  { scan_image  "$COMPONENT"; }
cmd_push()  { push_image  "$COMPONENT"; }

cmd_smoke() {
  # "Does it actually start" -- catches a bad CMD, a missing runtime dep;
  # assert GET / is 404 so a leaked static/ can't fight httproute.yaml for
  # routing; assert UID 1001;
  local ref name
  ref="$(image_ref "$COMPONENT")"
  name="smoke-backend-$$"
  trap 'docker rm -f "$name" >/dev/null 2>&1 || true' RETURN

  log "Starting $ref"
  docker run -d --name "$name" -p 18000:8000 \
    -e SESSION_SECRET=smoke-test-secret \
    -e AUTH_MODE=dev \
    -e GATE_SERVER_ID=smoke-test \
    -e ALLOWED_AGENT_ARNS='arn:aws:iam::123456789012:role/mcp-*' \
    "$ref" >/dev/null

  wait_for http://localhost:18000/api/healthz backend

  log "/api/healthz reports ok"
  curl -fsS http://localhost:18000/api/healthz | grep -q '"status":"ok"' \
    || fail "healthz did not report ok"

  log "backend does not serve the SPA"
  local code
  code="$(http_code http://localhost:18000/)"
  [ "$code" = 404 ] || fail "GET / returned $code, expected 404 -- static/ leaked into the backend image"

  log "runs non-root as UID 1001"
  [ "$(docker exec "$name" id -u)" = 1001 ] || fail "not running as UID 1001"

  log "backend smoke passed"
}

cmd_all() { cmd_test; cmd_build; cmd_scan; cmd_smoke; }

case "${1:-all}" in
  test|build|scan|smoke|push|all) "cmd_${1:-all}" ;;
  *) fail "unknown command '$1' (test|build|scan|smoke|push|all)" ;;
esac
