#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DURATION="60s"
BASE_URL="${BASE_URL:-http://localhost:8080}"

run_k6() {
  local vus="$1"
  local output="$2"
  local script="$3"

  docker run --rm \
    --network host \
    --user "$(id -u):$(id -g)" \
    -e "BASE_URL=$BASE_URL" \
    -v "$ROOT:/work" \
    -w /work \
    grafana/k6 run \
    --vus "$vus" \
    --duration "$DURATION" \
    --summary-export "performance/results/$output" \
    "performance/k6/$script"
}

for vus in 1 5 20 50 100; do
  run_k6 "$vus" "catalog-$vus"vus.json "catalog.js"
done

for vus in 1 5 20 50 100; do
  run_k6 "$vus" "search-$vus"vus.json "search.js"
done

for vus in 1 5 20 50 100; do
  run_k6 "$vus" "recommendations-$vus"vus.json "recommendations.js"
done

docker run --rm --user "$(id -u):$(id -g)" -v "$ROOT:/work" -w /work python:3.13-slim python performance/generate_charts.py
