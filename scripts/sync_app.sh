#!/usr/bin/env bash
# Databricks Apps only upload the app/ folder, so copy the shared package in.
# Run before `databricks bundle deploy` (or before deploying the app from the UI).
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf app/triage
cp -r src/triage app/triage
find app/triage -name "__pycache__" -prune -exec rm -rf {} +
echo "synced src/triage -> app/triage"
