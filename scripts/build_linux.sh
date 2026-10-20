#!/usr/bin/env bash
# ==============================================================================
# Build Script for Sutra AI (RM) Linux Installer Packages (.AppImage & .deb)
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_PUBLIC_DIR="$(cd "$APP_DIR/../Sutra-AI-Frontend/public" 2>/dev/null && pwd || echo "")"

echo "=========================================================="
echo "  Building Sutra AI (RM) for Linux (.AppImage + .deb)    "
echo "=========================================================="
echo "Working Directory: $APP_DIR"

cd "$APP_DIR"

# 1. Ensure Dependencies
if [ ! -d "node_modules" ]; then
    echo "[1/3] Installing dependencies with npm ci..."
    npm ci
else
    echo "[1/3] Dependencies found in node_modules."
fi

# 2. Build Linux Binaries using electron-builder
echo "[2/3] Compiling Electron App for Linux (AppImage & deb)..."
npx electron-builder --linux --x64

echo "[3/3] Verifying generated artifacts in $APP_DIR/dist_bin..."
ls -lh "$APP_DIR/dist_bin"

# 3. Optionally sync to Frontend public/ folder for local distribution
if [ -n "$FRONTEND_PUBLIC_DIR" ] && [ -d "$FRONTEND_PUBLIC_DIR" ]; then
    echo "Syncing built Linux binaries to Sutra-AI-Frontend/public/..."
    cp -vf "$APP_DIR/dist_bin/"*.AppImage "$FRONTEND_PUBLIC_DIR/" 2>/dev/null || true
    cp -vf "$APP_DIR/dist_bin/"*.deb "$FRONTEND_PUBLIC_DIR/" 2>/dev/null || true
    echo "✓ Frontend public folder updated."
fi

echo "=========================================================="
echo "✓ Linux packages successfully created!"
echo "Artifacts located in: $APP_DIR/dist_bin/"
echo "=========================================================="
