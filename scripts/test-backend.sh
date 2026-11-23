#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTEST="$ROOT/backend/.venv/bin/pytest"

# Run fast offline unit, fuzz, concurrency, and API tests first.
"$PYTEST" "$ROOT/backend/tests" --ignore="$ROOT/backend/tests/test_eleven.py" "$@"

# If TEST_DATABASE_URL is already set, pytest above already ran test_admin_db_integration.py.
if [[ -z "${TEST_DATABASE_URL:-}" ]]; then
  echo ""
  echo "Tip: run real-Postgres integration tests with:"
  echo "  TEST_DATABASE_URL=postgresql://localhost/quiz_game_test $0"
fi
