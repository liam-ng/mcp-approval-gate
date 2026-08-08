#!/usr/bin/env bash
# Executor pipeline. Usage: ./pipelines/executor/ci.sh <test|build|scan|smoke|push|all>
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

COMPONENT=executor

cmd_test() {
  # The executor has no test suite yet. What is worth catching here is the
  # class of failure that only shows up at container start: a module that does
  # not import, or a Settings model that cannot be constructed. settings.py
  # instantiates at import time, so importing the package IS the config check.
  log "import + settings validation"
  cd "$REPO_ROOT/executor"
  python -m pip install --quiet -r requirements.txt
  GATE_SERVER_ID=ci-import-check python -c "
import app.main, app.aws_exec, app.gate_client
from app.settings import settings
assert settings.dry_run is True, 'DRY_RUN must default to on'
assert settings.poll_interval_seconds > 0
print('executor imports clean; dry_run default is on')
"
}

cmd_build() { build_image "$COMPONENT"; }
cmd_scan()  { scan_image  "$COMPONENT"; }
cmd_push()  { push_image  "$COMPONENT"; }

cmd_smoke() {
  # No port and no healthz -- this is a polling client, so "does it work" is
  # asserted from behaviour: it must boot, announce itself, survive an
  # unreachable gate without dying (the kubelet restarting on every transient
  # poll failure is worse than logging it), and refuse to start on bad config.
  local ref name logs
  ref="$(image_ref "$COMPONENT")"
  name="smoke-executor-$$"
  trap 'docker rm -f "$name" >/dev/null 2>&1 || true' RETURN

  log "Starting $ref against an unreachable gate"
  docker run -d --name "$name" \
    -e GATE_SERVER_ID=smoke-test \
    -e GATE_BASE_URL=http://127.0.0.1:1 \
    -e POLL_INTERVAL_SECONDS=1 \
    -e POLL_JITTER_SECONDS=0 \
    -e REQUEST_TIMEOUT_SECONDS=2 \
    "$ref" >/dev/null

  # Two poll cycles at the 1s interval set above, plus start-up.
  local i
  for i in $(seq 1 15); do
    logs="$(docker logs "$name" 2>&1)"
    case "$logs" in *"poll failed"*) break ;; esac
    sleep 1
  done

  log "announces its configuration on boot"
  case "$logs" in
    *"executor starting"*) ;;
    *) fail "no startup line in logs:\n$logs" ;;
  esac

  log "DRY_RUN defaults to on and says so"
  case "$logs" in
    *"DRY_RUN is on"*) ;;
    *) fail "DRY_RUN was not on by default -- an unarmed scaffold must stay unarmed" ;;
  esac

  log "survives an unreachable gate instead of crash-looping"
  case "$logs" in
    *"poll failed"*) ;;
    *) fail "expected a logged poll failure against 127.0.0.1:1:\n$logs" ;;
  esac
  [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ] \
    || fail "container exited on a transient poll failure"

  log "runs non-root as UID 1001"
  [ "$(docker exec "$name" id -u)" = 1001 ] || fail "not running as UID 1001"

  log "refuses to start without GATE_SERVER_ID"
  # settings.py validates at import, so this must be a non-zero exit and not a
  # process that polls as nobody.
  if docker run --rm -e GATE_BASE_URL=http://127.0.0.1:1 "$ref" >/dev/null 2>&1; then
    fail "started with no GATE_SERVER_ID -- settings validation is not gating boot"
  fi

  log "executor smoke passed"
}

cmd_all() { cmd_test; cmd_build; cmd_scan; cmd_smoke; }

case "${1:-all}" in
  test|build|scan|smoke|push|all) "cmd_${1:-all}" ;;
  *) fail "unknown command '$1' (test|build|scan|smoke|push|all)" ;;
esac
