#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUM_BIN="$REPO_DIR/third_party/gum/linux-arm64/gum"
EXPECTED_SHA256="dc0fddd487fbebc563b04a48fdabc6f14c7f58505d4022241cd1d9037fa86698"

[ -x "$GUM_BIN" ]
[ "$(sha256sum "$GUM_BIN" | awk '{print $1}')" = "$EXPECTED_SHA256" ]
"$GUM_BIN" --version | grep -q 'gum version v0.17.0'
grep -q 'Copyright (c) 2022-2024 Charmbracelet, Inc' \
  "$REPO_DIR/third_party/gum/LICENSE"
grep -q 'archive_sha256=b0b9ed95cbf7c8b7073f17b9591811f5c001e33c7cfd066ca83ce8a07c576f9c' \
  "$REPO_DIR/third_party/gum/VERSION"
grep -q 'binary_sha256=dc0fddd487fbebc563b04a48fdabc6f14c7f58505d4022241cd1d9037fa86698' \
  "$REPO_DIR/third_party/gum/VERSION"
grep -q 'third_party/gum/LICENSE' "$REPO_DIR/THIRD_PARTY_NOTICES.md"
# Gum resolution lives in shared scripts/ui.sh (wizard + home source it).
grep -q 'scripts/ui.sh' "$REPO_DIR/wizard.sh"
grep -q 'VENDORED_GUM=' "$REPO_DIR/scripts/ui.sh"
grep -q 'GUM_BIN' "$REPO_DIR/scripts/ui.sh"
grep -q 'scripts/ui.sh' "$REPO_DIR/scripts/home.sh"
