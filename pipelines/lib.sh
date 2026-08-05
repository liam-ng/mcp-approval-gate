#!/usr/bin/env bash
# Shared helpers for pipelines/backend and pipelines/frontend. 
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT

# The ACR login server, e.g. REGISTRY=myacr-abc123.azurecr.io. Deliberately
# not defaulted to a real registry: a stale hardcoded host silently produces an
# image ref nothing built or pushed, which surfaces as a confusing pull-auth
# error rather than "you forgot to set REGISTRY". `local` builds without one.
export REGISTRY="${REGISTRY:-local}"

# Immutable tag deployments pin; Tag format: <registry>/mcp-approval-gate-<dev|main>:<sha>
export IMAGE_TAG="${IMAGE_TAG:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)}"
export MOVING_TAG="${MOVING_TAG:-}"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

image_ref() { echo "${REGISTRY}/mcp-approval-gate-$1:${IMAGE_TAG}"; }

build_image() {
  # Context is ./<component>, not the repo root, so one half's changes can't
  # invalidate the other's layer cache
  local component="$1" ref
  ref="$(image_ref "$component")"
  log "Building $ref"
  docker build -t "$ref" "$REPO_ROOT/$component"
}

scan_image() {
  # Hard gate: --exit-code 1 fails before any push. --ignore-unfixed is deliberate  
  local ref
  ref="$(image_ref "$1")"
  command -v trivy >/dev/null 2>&1 || fail "trivy not found"
  log "Scanning $ref for CRITICAL CVEs"
  trivy image --severity CRITICAL --ignore-unfixed --exit-code 1 --no-progress "$ref"
}

push_image() {
  # Assumes the caller already authenticated (`az acr login --name <registry>`)
  local component="$1" ref
  ref="$(image_ref "$component")"
  log "Pushing $ref"
  docker push "$ref"
  if [ -n "$MOVING_TAG" ]; then
    local moving="${REGISTRY}/mcp-approval-gate-${component}:${MOVING_TAG}"
    log "Pushing $moving"
    docker tag "$ref" "$moving"
    docker push "$moving"
  fi
}

wait_for() {
  # Poll, don't sleep -- container start time varies with runner load
  local url="$1" name="$2"
  for _ in $(seq 1 30); do
    curl -fsS "$url" >/dev/null 2>&1 && return 0
    sleep 1
  done
  fail "$name never became ready at $url"
}

http_code() { curl -s -o /dev/null -w '%{http_code}' "$1"; }
