#!/usr/bin/env bash
# Install OpenMontage (pinned) and add the chad (chadgpt.ru) image/video tools.
#
#   ./install.sh                 # OpenMontage goes next to this repo: ../OpenMontage
#   OPENMONTAGE_DIR=~/om ./install.sh
#   SKIP_SETUP=1 ./install.sh    # only copy the chad tools into an existing install
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OM_DIR="${OPENMONTAGE_DIR:-$(dirname "$HERE")/OpenMontage}"
OM_REPO="https://github.com/calesthio/OpenMontage.git"
OM_REF="${OPENMONTAGE_REF:-fecb9a1e3cc23ef84de30b272059457a427f16f8}"

if [ ! -d "$OM_DIR/.git" ]; then
  echo "==> Cloning OpenMontage @ ${OM_REF:0:12} into $OM_DIR"
  mkdir -p "$OM_DIR"
  git -C "$OM_DIR" init -q
  git -C "$OM_DIR" remote add origin "$OM_REPO"
  git -C "$OM_DIR" fetch -q --depth 1 origin "$OM_REF"
  git -C "$OM_DIR" checkout -q FETCH_HEAD
else
  echo "==> Using existing OpenMontage at $OM_DIR ($(git -C "$OM_DIR" rev-parse --short HEAD))"
fi

if [ "${SKIP_SETUP:-0}" != "1" ] && [ ! -x "$OM_DIR/.venv/bin/python" ]; then
  echo "==> make setup (Python venv, Remotion npm deps, piper-tts)"
  make -C "$OM_DIR" setup
fi

echo "==> Adding chad tools"
cp -R "$HERE/overlay/." "$OM_DIR/"

ENV_FILE="$OM_DIR/.env"
[ -f "$ENV_FILE" ] || cp "$OM_DIR/.env.example" "$ENV_FILE"
if ! grep -q '^CHADGPT_API_KEY=' "$ENV_FILE"; then
  {
    echo ""
    echo "# --- chad (chadgpt.ru) --- key from ask.chadgpt.ru -> Chad API."
    echo "# A CHADGPT_API_KEY set in the process environment takes precedence."
    echo "CHADGPT_API_KEY="
    echo "# Set to 1 only after a manual test clip: chad video API is undocumented."
    echo "CHADGPT_VIDEO_ENABLED="
  } >> "$ENV_FILE"
fi

PY="$OM_DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Done (no .venv yet: run 'make -C $OM_DIR setup', then this script again)."
  exit 0
fi

if "$PY" -c "import pytest" 2>/dev/null; then
  echo "==> Running chad adapter tests"
  (cd "$OM_DIR" && .venv/bin/python -m pytest tests/tools/test_chadgpt.py -q -p no:cacheprovider)
fi

(cd "$OM_DIR" && "$PY" - <<'PY'
from tools.graphics.chadgpt_image import ChadgptImage
from tools.video.chadgpt_video import ChadgptVideo
print(f"chadgpt_image: {ChadgptImage().get_status().value}")
print(f"chadgpt_video: {ChadgptVideo().get_status().value}")
PY
)
echo "Done. OpenMontage: $OM_DIR"
