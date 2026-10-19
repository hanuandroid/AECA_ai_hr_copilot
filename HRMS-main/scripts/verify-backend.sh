#!/usr/bin/env sh
# Backend verification in a throwaway Python 3.11 container: full pytest suite, offline AI evaluations and the CI
# advisory-gate tests. No local Python needed; nothing on the host is written.
#
#   sh HRMS-main/scripts/verify-backend.sh            # from the repository root (Git Bash, Linux, macOS)
#   VERIFY_IMAGE=python:3.11 sh HRMS-main/scripts/verify-backend.sh
#
# Safety: the repository is mounted read-only and copied inside the container WITHOUT backend/.env, backend/storage
# (the local database, uploads and model cache), frontend/node_modules and untracked workspace notes; provider keys are
# blanked, so no real model can be called. Tests build their own temporary SQLite database.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
IMAGE=${VERIFY_IMAGE:-python:3.11}
if command -v cygpath >/dev/null 2>&1; then  # Git Bash on Windows: hand Docker a Windows path, unconverted
  ROOT=$(cygpath -w "$ROOT")
  export MSYS_NO_PATHCONV=1
fi

docker run --rm -e OPENAI_API_KEY= -e ANTHROPIC_API_KEY= -v "$ROOT:/repo:ro" "$IMAGE" sh -c '
set -e
git config --global --add safe.directory "*"
mkdir /work
tar -C /repo --exclude=./HRMS-main/backend/.env --exclude=./HRMS-main/backend/storage \
    --exclude=./HRMS-main/frontend/node_modules --exclude=./HRMS-main/frontend/.next \
    --exclude="./writing-block*" -cf - . | tar -C /work -xf -
cd /work/HRMS-main/backend
pip install -q --disable-pip-version-check -r requirements-dev.txt
python -c "import sys, fastapi; print(\"python\", sys.version.split()[0], \"fastapi\", fastapi.__version__)"
echo "== backend tests";        python -m pytest -q -p no:cacheprovider
echo "== offline evaluations";  python -m evals.run_evals --results-dir /tmp/eval-results
echo "== advisory-gate tests";  cd /work && python -m pytest -q -p no:cacheprovider .github/scripts
'
