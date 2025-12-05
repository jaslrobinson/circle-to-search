#!/bin/bash
set -e

# Circle to Search - AppImage Build Script
# This script creates a self-contained AppImage with all dependencies

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
APPDIR="${BUILD_DIR}/AppDir"

echo "=== Circle to Search AppImage Builder ==="

# Check for required tools
check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo "Error: $1 is required but not installed."
        echo "Install it with: $2"
        exit 1
    fi
}

check_command "python3" "Your package manager (e.g., pacman -S python)"
check_command "pip" "Your package manager (e.g., pacman -S python-pip)"

# Clean previous build
echo "[1/7] Cleaning previous build..."
rm -rf "${BUILD_DIR}"
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/lib"
mkdir -p "${APPDIR}/usr/share/circle-to-search"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${APPDIR}/usr/share/tessdata"

# Copy main application
echo "[2/7] Copying application files..."
cp "${SCRIPT_DIR}/circle-to-search.py" "${APPDIR}/usr/share/circle-to-search/"
cp "${SCRIPT_DIR}/AppDir/circle-to-search.desktop" "${APPDIR}/"
cp "${SCRIPT_DIR}/AppDir/circle-to-search.desktop" "${APPDIR}/usr/share/applications/"

# Copy icon if it exists, otherwise create a simple one
if [ -f "${SCRIPT_DIR}/icon.png" ]; then
    cp "${SCRIPT_DIR}/icon.png" "${APPDIR}/circle-to-search.png"
    cp "${SCRIPT_DIR}/icon.png" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/circle-to-search.png"
else
    echo "Warning: No icon.png found. Creating placeholder..."
    # Create a simple SVG icon as fallback
    cat > "${APPDIR}/circle-to-search.svg" << 'SVGEOF'
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <circle cx="128" cy="128" r="100" fill="none" stroke="#89b4fa" stroke-width="12" stroke-dasharray="30 10"/>
  <circle cx="180" cy="180" r="40" fill="#89b4fa"/>
  <rect x="200" y="200" width="40" height="12" rx="6" fill="#89b4fa" transform="rotate(45 220 206)"/>
</svg>
SVGEOF
    cp "${APPDIR}/circle-to-search.svg" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/"
fi

# Copy AppRun
cp "${SCRIPT_DIR}/AppDir/AppRun" "${APPDIR}/"
chmod +x "${APPDIR}/AppRun"

# Create Python virtual environment with all dependencies
echo "[3/7] Creating Python environment with dependencies..."
python3 -m venv "${BUILD_DIR}/venv"
source "${BUILD_DIR}/venv/bin/activate"

# Install Python dependencies
pip install --upgrade pip
pip install PyGObject Pillow pytesseract

# Copy Python from system (we need the full interpreter)
echo "[4/7] Bundling Python interpreter..."
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_PREFIX=$(python3 -c "import sys; print(sys.prefix)")

# Copy Python binary
cp "$(which python3)" "${APPDIR}/usr/bin/"

# Copy Python standard library
mkdir -p "${APPDIR}/usr/lib/python${PYTHON_VERSION}"
cp -r "${PYTHON_PREFIX}/lib/python${PYTHON_VERSION}/"* "${APPDIR}/usr/lib/python${PYTHON_VERSION}/" 2>/dev/null || true

# Copy site-packages from venv
mkdir -p "${APPDIR}/usr/lib/python${PYTHON_VERSION}/site-packages"
cp -r "${BUILD_DIR}/venv/lib/python${PYTHON_VERSION}/site-packages/"* "${APPDIR}/usr/lib/python${PYTHON_VERSION}/site-packages/"

# Copy system binaries
echo "[5/7] Bundling system binaries..."
for cmd in grim wl-copy curl; do
    if command -v "$cmd" &> /dev/null; then
        cp "$(which $cmd)" "${APPDIR}/usr/bin/"
        echo "  - Bundled: $cmd"
    else
        echo "  - Warning: $cmd not found (some features may not work)"
    fi
done

# Bundle tesseract if available
if command -v tesseract &> /dev/null; then
    cp "$(which tesseract)" "${APPDIR}/usr/bin/"
    echo "  - Bundled: tesseract"

    # Copy tessdata (language files)
    if [ -d "/usr/share/tessdata" ]; then
        cp /usr/share/tessdata/eng.* "${APPDIR}/usr/share/tessdata/" 2>/dev/null || true
        echo "  - Bundled: English tessdata"
    fi
fi

# Copy required shared libraries
echo "[6/7] Bundling shared libraries..."
copy_lib_deps() {
    local binary="$1"
    ldd "$binary" 2>/dev/null | grep "=> /" | awk '{print $3}' | while read lib; do
        if [ -f "$lib" ] && [ ! -f "${APPDIR}/usr/lib/$(basename $lib)" ]; then
            # Skip system libraries that should not be bundled
            case "$(basename $lib)" in
                libc.so*|libm.so*|libdl.so*|librt.so*|libpthread.so*|ld-linux*|libGL.so*|libX*.so*|libwayland*.so*)
                    ;;
                *)
                    cp "$lib" "${APPDIR}/usr/lib/" 2>/dev/null || true
                    ;;
            esac
        fi
    done
}

# Copy GTK and GObject introspection files
mkdir -p "${APPDIR}/usr/lib/girepository-1.0"
for typelib in Gtk-3.0 Gdk-3.0 GdkPixbuf-2.0 GLib-2.0 GObject-2.0 Gio-2.0 cairo-1.0 Pango-1.0 PangoCairo-1.0; do
    find /usr/lib -name "${typelib}.typelib" -exec cp {} "${APPDIR}/usr/lib/girepository-1.0/" \; 2>/dev/null || true
done

# Copy library dependencies for bundled binaries
for bin in "${APPDIR}/usr/bin/"*; do
    copy_lib_deps "$bin"
done

deactivate

# Download appimagetool if not present
echo "[7/7] Building AppImage..."
if [ ! -f "${BUILD_DIR}/appimagetool" ]; then
    echo "Downloading appimagetool..."
    wget -q "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" \
        -O "${BUILD_DIR}/appimagetool"
    chmod +x "${BUILD_DIR}/appimagetool"
fi

# Build the AppImage
cd "${BUILD_DIR}"
ARCH=x86_64 "${BUILD_DIR}/appimagetool" "${APPDIR}" "${SCRIPT_DIR}/Circle_to_Search-x86_64.AppImage"

echo ""
echo "=== Build Complete ==="
echo "AppImage created: ${SCRIPT_DIR}/Circle_to_Search-x86_64.AppImage"
echo ""
echo "Note: Users still need these Wayland tools installed on their system:"
echo "  - A Wayland compositor (Sway, Hyprland, GNOME Wayland, etc.)"
echo "  - xdg-open (usually pre-installed)"
echo "  - notify-send (for notifications)"
echo ""
echo "The AppImage bundles: Python, GTK, grim, wl-copy, curl, tesseract"
