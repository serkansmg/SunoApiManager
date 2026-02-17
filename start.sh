#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Suno Manager — One-click launcher (macOS & Linux)
#
# Activates the conda environment and starts the server.
# Usage:
#   ./start.sh              → start on default port (config.yaml)
#   ./start.sh --port 9090  → override port
#   ./start.sh --no-reload  → disable hot-reload (production)
# ─────────────────────────────────────────────────────────────

ENV_NAME="suno-manager"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

err()  { echo -e "${RED}[ERROR]${NC} $1"; }
info() { echo -e "${CYAN}[INFO]${NC}  $1"; }

# ── Parse arguments ───────────────────────────────────────

PORT=""
RELOAD="--reload"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            PORT="$2"
            shift 2
            ;;
        --no-reload)
            RELOAD=""
            shift
            ;;
        *)
            echo "Usage: $0 [--port PORT] [--no-reload]"
            exit 1
            ;;
    esac
done

# ── Find and activate conda ──────────────────────────────

activate_conda() {
    # Already available?
    if command -v conda &>/dev/null; then
        return 0
    fi

    # Search common conda locations
    for CONDA_PATH in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "/opt/conda" "/usr/local/miniconda3"; do
        if [ -f "$CONDA_PATH/bin/conda" ]; then
            eval "$("$CONDA_PATH/bin/conda" shell.bash hook)"
            return 0
        fi
    done

    return 1
}

if ! activate_conda; then
    err "Conda not found. Run ./install.sh first."
    exit 1
fi

# ── Check environment exists ─────────────────────────────

if ! conda env list | grep -qw "$ENV_NAME"; then
    err "Conda environment '$ENV_NAME' not found. Run ./install.sh first."
    exit 1
fi

# ── Activate environment ─────────────────────────────────

CONDA_BASE="$(conda info --base)"
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$ENV_NAME"

# ── Read port from config.yaml if not overridden ─────────

cd "$SCRIPT_DIR"

if [ -z "$PORT" ]; then
    if [ -f "config.yaml" ]; then
        # Extract port from config.yaml (simple grep, no yq dependency)
        PORT=$(python -c "
import yaml
with open('config.yaml') as f:
    cfg = yaml.safe_load(f)
print(cfg.get('server', {}).get('port', 8080))
" 2>/dev/null || echo "8080")
    else
        PORT="8080"
    fi
fi

# ── Check if port is already in use ──────────────────────

if lsof -i :"$PORT" -sTCP:LISTEN &>/dev/null; then
    EXISTING_PID=$(lsof -t -i :"$PORT" -sTCP:LISTEN 2>/dev/null | head -1)
    echo -e "${YELLOW}[WARN]${NC}  Port $PORT is already in use (PID: $EXISTING_PID)"
    read -rp "Kill existing process and restart? [y/N] " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        kill "$EXISTING_PID" 2>/dev/null || true
        sleep 1
        info "Old process killed"
    else
        err "Aborted. Free port $PORT or use --port to specify another."
        exit 1
    fi
fi

# ── Start the server ─────────────────────────────────────

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Suno Manager — Starting          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Environment:  ${CYAN}$ENV_NAME${NC}"
echo -e "  Python:       ${CYAN}$(python --version)${NC}"
echo -e "  Server:       ${CYAN}http://localhost:$PORT${NC}"
echo -e "  Swagger:      ${CYAN}http://localhost:$PORT/docs${NC}"
echo -e "  Reload:       ${CYAN}${RELOAD:-disabled}${NC}"
echo ""

exec uvicorn app:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    $RELOAD \
    --log-level info
