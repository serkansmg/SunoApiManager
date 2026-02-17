#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Suno Manager — One-click installer (macOS & Linux)
#
# What this does:
#   1. Installs Miniconda if conda is not found
#   2. Creates a conda environment "suno-manager" with Python 3.12
#   3. Installs ffmpeg (via conda-forge)
#   4. Installs Python dependencies from requirements.txt
#   5. Installs Playwright + Chromium (for CAPTCHA solving)
#   6. Creates default config.yaml if missing
#   7. Creates required directories
# ─────────────────────────────────────────────────────────────

ENV_NAME="suno-manager"
PYTHON_VERSION="3.12"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║       Suno Manager — Installer           ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Check / Install Conda ──────────────────────────

if command -v conda &>/dev/null; then
    ok "Conda found: $(conda --version)"
else
    info "Conda not found. Installing Miniconda..."

    OS="$(uname -s)"
    ARCH="$(uname -m)"

    case "$OS" in
        Darwin)
            case "$ARCH" in
                arm64)  MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-arm64.sh" ;;
                x86_64) MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh" ;;
                *)      err "Unsupported macOS architecture: $ARCH"; exit 1 ;;
            esac
            ;;
        Linux)
            case "$ARCH" in
                x86_64)  MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh" ;;
                aarch64) MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh" ;;
                *)       err "Unsupported Linux architecture: $ARCH"; exit 1 ;;
            esac
            ;;
        *)
            err "Unsupported OS: $OS (only macOS and Linux are supported)"
            exit 1
            ;;
    esac

    INSTALLER="/tmp/miniconda_installer.sh"
    info "Downloading Miniconda from $MINICONDA_URL ..."
    curl -fsSL "$MINICONDA_URL" -o "$INSTALLER"
    chmod +x "$INSTALLER"

    info "Installing Miniconda to ~/miniconda3 ..."
    bash "$INSTALLER" -b -p "$HOME/miniconda3"
    rm -f "$INSTALLER"

    # Initialize conda for the current shell
    eval "$("$HOME/miniconda3/bin/conda" shell.bash hook)"

    # Add conda init to shell profile if not already there
    SHELL_NAME="$(basename "$SHELL")"
    case "$SHELL_NAME" in
        zsh)  PROFILE="$HOME/.zshrc" ;;
        bash) PROFILE="$HOME/.bashrc" ;;
        *)    PROFILE="$HOME/.bashrc" ;;
    esac

    if ! grep -q "conda initialize" "$PROFILE" 2>/dev/null; then
        info "Adding conda init to $PROFILE ..."
        "$HOME/miniconda3/bin/conda" init "$SHELL_NAME" &>/dev/null || true
    fi

    ok "Miniconda installed successfully"
fi

# Make sure conda is available in this script
if ! command -v conda &>/dev/null; then
    # Try common conda locations
    for CONDA_PATH in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "/opt/conda" "/usr/local/miniconda3"; do
        if [ -f "$CONDA_PATH/bin/conda" ]; then
            eval "$("$CONDA_PATH/bin/conda" shell.bash hook)"
            break
        fi
    done
fi

if ! command -v conda &>/dev/null; then
    err "Conda still not available. Please restart your terminal and run this script again."
    exit 1
fi

# ── Step 2: Create Conda Environment ──────────────────────

if conda env list | grep -qw "$ENV_NAME"; then
    ok "Conda environment '$ENV_NAME' already exists"
else
    info "Creating conda environment '$ENV_NAME' (Python $PYTHON_VERSION) ..."
    conda create -n "$ENV_NAME" python="$PYTHON_VERSION" -y -q
    ok "Environment '$ENV_NAME' created"
fi

# Activate the environment
info "Activating environment '$ENV_NAME' ..."
conda activate "$ENV_NAME" 2>/dev/null || {
    # Fallback: source activate directly
    CONDA_BASE="$(conda info --base)"
    source "$CONDA_BASE/etc/profile.d/conda.sh"
    conda activate "$ENV_NAME"
}
ok "Environment active: $(python --version)"

# ── Step 3: Install ffmpeg via conda ──────────────────────

if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg found: $(ffmpeg -version 2>&1 | head -1)"
else
    info "Installing ffmpeg via conda-forge ..."
    conda install -c conda-forge ffmpeg -y -q
    ok "ffmpeg installed"
fi

# ── Step 4: Install Python Dependencies ───────────────────

info "Installing Python dependencies ..."
cd "$SCRIPT_DIR"

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt -q
    ok "Python dependencies installed"
else
    err "requirements.txt not found in $SCRIPT_DIR"
    exit 1
fi

# ── Step 5: Install Playwright (optional, for CAPTCHA) ────

info "Installing Playwright + Chromium (for CAPTCHA solving) ..."
pip install playwright -q
python -m playwright install chromium 2>/dev/null || {
    warn "Playwright Chromium install failed (non-critical — only needed for CAPTCHA)"
}
ok "Playwright installed"

# ── Step 6: Create Default Config ─────────────────────────

cd "$SCRIPT_DIR"

if [ ! -f "config.yaml" ]; then
    info "Creating default config.yaml ..."
    cat > config.yaml << 'YAML'
# Suno Manager Configuration

# Suno API connection
suno_api:
  # Suno session cookie — __client cookie value from browser
  cookie: ""

# Song generation settings
generation:
  default_model: "chirp-crow"
  min_duration_filter: 180
  polling_interval: 10
  auto_download: true
  auto_analyze_silence: true
  batch_size: 5
  batch_delay: 30

# Download settings
download:
  directory: "./downloads"
  format: "wav"

# Silence analysis settings
silence_analysis:
  threshold: -40
  min_length: 1000

# Server settings
server:
  host: "0.0.0.0"
  port: 8080
YAML
    warn "config.yaml created with empty cookie — you must set your Suno cookie before use!"
else
    ok "config.yaml already exists"
fi

# ── Step 7: Create Required Directories ───────────────────

mkdir -p "$SCRIPT_DIR/downloads"
mkdir -p "$SCRIPT_DIR/uploads"
mkdir -p "$SCRIPT_DIR/logs"
ok "Directories ready (downloads, uploads, logs)"

# ── Done ──────────────────────────────────────────────────

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       Installation Complete!              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Environment:  ${CYAN}$ENV_NAME${NC}"
echo -e "  Python:       ${CYAN}$(python --version)${NC}"
echo -e "  Project:      ${CYAN}$SCRIPT_DIR${NC}"
echo ""
echo -e "  ${YELLOW}Next steps:${NC}"
echo -e "    1. Edit ${CYAN}config.yaml${NC} and set your Suno cookie"
echo -e "    2. Run ${CYAN}./start.sh${NC} to start the server"
echo ""
