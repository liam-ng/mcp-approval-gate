#!/usr/bin/env bash
# Shared helpers for the per-component pipelines in pipelines/backend and
# pipelines/frontend.
#
# The real CI logic lives in these scripts rather than in workflow YAML so it
# can be run locally (`./pipelines/backend/ci.sh test`) and isn't locked into
# one CI system. GitHub Actions can only execute workflows from
# .github/workflows/, so those files must live there; all they do is call in
# here.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT

# Override locally to build without an ACR, e.g. REGISTRY=local.
export REGISTRY="${REGISTRY:-liammcp-fxb8azcnexhncbd9.azurecr.io}"

# Immutable, traceable tag: the commit SHA. Deployments should pin this;
# MOVING_TAG (e.g. "main") is published alongside it for convenience.
export IMAGE_TAG="${IMAGE_TAG:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)}"
export MOVING_TAG="${MOVING_TAG:-}"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

image_ref() { echo "${REGISTRY}/mcp-approval-gate-$1:${IMAGE_TAG}"; }

# Build <component> from ./<component> -- the context is the component dir,
# not the repo root, so a change to one half can't invalidate the other's
# layer cache.
build_image() {
  local component="$1" ref
  ref="$(image_ref "$component")"
  log "Building $ref"
  docker build -t "$ref" "$REPO_ROOT/$component"
}

# Hard gate on CRITICAL CVEs: --exit-code 1 makes this fail the pipeline
# before any push. --ignore-unfixed is deliberate -- base-image CVEs with no
# available patch aren't actionable, and failing on them just trains people to
# bypass the gate. Drop it to fail on every CRITICAL regardless of fixability.
scan_image() {
  local ref
  ref="$(image_ref "$1")"
  command -v trivy >/dev/null 2>&1 || fail "trivy not found"
  log "Scanning $ref for CRITICAL CVEs"
  trivy image --severity CRITICAL --ignore-unfixed --exit-code 1 --no-progress "$ref"
}

# Assumes the caller is already authenticated: `azure/login` + `az acr login`
# in CI, or `az acr login --name <registry>` locally.
push_image() {
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

# Poll until an endpoint answers -- container start time varies with runner load.
wait_for() {
  local url="$1" name="$2"
  for _ in $(seq 1 30); do
    curl -fsS "$url" >/dev/null 2>&1 && return 0
    sleep 1
  done
  fail "$name never became ready at $url"
}

http_code() { curl -s -o /dev/null -w '%{http_code}' "$1"; }
