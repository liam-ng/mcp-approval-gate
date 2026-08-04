#!/usr/bin/env bash
# Frontend pipeline. Usage: ./pipelines/frontend/ci.sh <test|build|scan|smoke|push|all>
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

COMPONENT=frontend

cmd_test() {
  log "tsc --noEmit && vite build"
  # `npm run build` is both the type check and the build, so a type error
  # fails here rather than shipping a broken bundle.
  cd "$REPO_ROOT/frontend"
  npm ci --no-audit --no-fund
  npm run build
}

cmd_build() { build_image "$COMPONENT"; }
cmd_scan()  { scan_image  "$COMPONENT"; }
cmd_push()  { push_image  "$COMPONENT"; }

cmd_smoke() {
  local ref name
  ref="$(image_ref "$COMPONENT")"
  name="smoke-frontend-$$"
  trap 'docker rm -f "$name" >/dev/null 2>&1 || true' RETURN

  log "Starting $ref"
  docker run -d --name "$name" -p 18080:8080 "$ref" >/dev/null
  wait_for http://localhost:18080/ frontend

  # Every client-side route must fall back to index.html -- react-router
  # resolves them in the browser, there's no file on disk. /act especially:
  # it's the email approve/reject landing page, reached by people with no
  # session, so a 404 there is a silently broken approval path.
  log "SPA fallback for client-side routes"
  local path code
  for path in / /login /tickets/abc "/act?token=x"; do
    code="$(http_code "http://localhost:18080$path")"
    [ "$code" = 200 ] || fail "GET $path returned $code, expected 200"
  done

  log "unknown assets still 404 (fallback must not mask missing files)"
  code="$(http_code http://localhost:18080/assets/does-not-exist.js)"
  [ "$code" = 404 ] || fail "GET /assets/does-not-exist.js returned $code, expected 404"

  log "runs non-root as UID 101"
  [ "$(docker exec "$name" id -u)" = 101 ] || fail "not running as UID 101"

  log "frontend smoke passed"
}

cmd_all() { cmd_test; cmd_build; cmd_scan; cmd_smoke; }

case "${1:-all}" in
  test|build|scan|smoke|push|all) "cmd_${1:-all}" ;;
  *) fail "unknown command '$1' (test|build|scan|smoke|push|all)" ;;
esac
