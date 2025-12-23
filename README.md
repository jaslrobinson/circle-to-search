# Circle to Search - Wayland Edition

A Linux utility that mimics Google's "Circle to Search" feature. Draw a selection on your screen, then search with Google Lens or extract text via OCR.

![Demo](demo.gif)

## Features

- **Freeform Selection**: Draw circles or any shape around content you want to search
- **Rectangle/Ellipse Mode**: Hold `Ctrl` for rectangles, `Ctrl+Shift` for ellipses
- **Google Lens Integration**: Upload selection and search directly with Google Lens
- **OCR Text Extraction**: Extract text from images using Tesseract
- **Multi-Desktop Support**: Works on Hyprland, Sway, KDE Plasma, GNOME, and more
- **Clipboard Support**: Selections are automatically copied to clipboard
- **HiDPI Support**: Works correctly on high-DPI displays
- **Dark Theme**: Modern dark UI with purple/pink neon glow effects

## Selection Modes

### Static Mode (Default)
```bash
./circle-to-search.py
./circle-to-search.py --static
```
- Takes a screenshot first, then displays it as the overlay background
- **Works on all Wayland compositors** (Hyprland, Sway, GNOME, KDE, etc.)
- Screen content is frozen while you draw your selection
- Most reliable option

### Live Mode (Hyprland/Sway Only)
```bash
./circle-to-search.py --live
```
- Uses layer-shell to create a transparent overlay on the live desktop
- **Requires**: Hyprland or Sway (wlroots-based compositors)
- **Requires**: `gtk-layer-shell` package
- See your actual live screen while drawing
- Captures the selected region after you finish drawing

#### Live Mode Limitations
- Only works on wlroots-based compositors (Hyprland, Sway)
- Windows may change appearance (e.g., transparency) when overlay takes focus
- If `gtk-layer-shell` is not installed, falls back to static mode automatically

## Installation

### Option 1: AppImage (Recommended)

Download the latest AppImage from the [Releases](../../releases) page:

```bash
chmod +x Circle_to_Search-x86_64.AppImage
./Circle_to_Search-x86_64.AppImage
```

### Option 2: Manual Installation

#### Dependencies

**Arch Linux:**
```bash
sudo pacman -S python python-pillow python-gobject gtk3 grim wl-clipboard
# Optional for OCR:
sudo pacman -S tesseract tesseract-data-eng python-pytesseract
# Optional for live mode:
sudo pacman -S gtk-layer-shell
```

**Fedora:**
```bash
sudo dnf install python3 python3-pillow python3-gobject gtk3 grim wl-clipboard
# Optional for OCR:
sudo dnf install tesseract tesseract-langpack-eng python3-pytesseract
# Optional for live mode:
sudo dnf install gtk-layer-shell
```

**Ubuntu/Debian (22.04+ with Wayland):**
```bash
sudo apt install python3 python3-pil python3-gi gir1.2-gtk-3.0 grim wl-clipboard
# Optional for OCR:
sudo apt install tesseract-ocr tesseract-ocr-eng python3-pytesseract
# Optional for live mode:
sudo apt install gtk-layer-shell
```

#### Run

```bash
git clone https://github.com/jaslrobinson/circle-to-search.git
cd circle-to-search
chmod +x circle-to-search.py
./circle-to-search.py
```

## Usage

1. **Launch** the application (bind to a keyboard shortcut for best experience)
2. **Draw** a circle/shape around the area you want to search
3. **Choose** an action:
   - **Search with Google Lens**: Uploads to imgur and opens Google Lens automatically
   - **Manual Paste**: Opens Google Lens (paste image manually)
   - **Extract Text**: Extracts text using Tesseract OCR

### Keyboard Shortcuts (while drawing)

| Key | Action |
|-----|--------|
| `Ctrl` + drag | Draw rectangle |
| `Ctrl+Shift` + drag | Draw ellipse |
| `Escape` | Cancel |

### Recommended: Bind to Keyboard Shortcuts

For the best experience, bind both modes to keyboard shortcuts:

**Hyprland** (`~/.config/hyprland/hyprland.conf`):
```
# Static mode
bind = $mainMod, S, exec, /path/to/circle-to-search.py --static

# Live mode
bind = $mainMod SHIFT, S, exec, /path/to/circle-to-search.py --live
```

**Sway** (`~/.config/sway/config`):
```
# Static mode
bindsym $mod+s exec /path/to/circle-to-search.py --static

# Live mode
bindsym $mod+Shift+s exec /path/to/circle-to-search.py --live
```
Users can now install it with:
yay -S circle-to-search

or
paru -S circle-to-search


## Building AppImage

To build the AppImage yourself:

```bash
./build-appimage.sh
```

This creates `Circle_to_Search-x86_64.AppImage` with all dependencies bundled.

**Note**: The AppImage bundles Python, GTK, grim, wl-copy, curl, and tesseract. Users only need:
- A Wayland compositor
- `xdg-open` (usually pre-installed)
- `notify-send` (for notifications)

## Requirements

- **Wayland** compositor (Sway, Hyprland, GNOME Wayland, KDE Wayland, etc.)
- This tool does **not** work on X11

| Feature | Static Mode | Live Mode |
|---------|-------------|-----------|
| All Wayland compositors | Yes | No (Hyprland/Sway only) |
| Live screen view | No (frozen screenshot) | Yes |
| gtk-layer-shell required | No | Yes |
| Window state preserved | Yes | No (may change focus/opacity) |

## How It Works

### Static Mode
1. Takes a fullscreen screenshot (auto-detects: `grim`, `spectacle`, or `gnome-screenshot`)
2. Displays the screenshot as an overlay background
3. You draw your selection on the frozen image
4. Crops the selected region
5. Copies the selection to clipboard via `wl-copy`
6. Presents options: Google Lens search or OCR text extraction

### Live Mode
1. Creates a transparent layer-shell overlay above all windows
2. You draw your selection while seeing the live desktop
3. Overlay becomes fully transparent, then captures the region
4. Copies the selection to clipboard via `wl-copy`
5. Presents options: Google Lens search or OCR text extraction

### Screenshot Tool Support

The app automatically detects and uses the appropriate screenshot tool:

| Tool | Desktop Environment |
|------|---------------------|
| `grim` | Hyprland, Sway (wlroots) |
| `spectacle` | KDE Plasma |
| `gnome-screenshot` | GNOME |

## License

MIT License - See [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.
