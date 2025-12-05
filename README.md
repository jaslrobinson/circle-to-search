# Circle to Search - Wayland Edition

A Linux utility that mimics Google's "Circle to Search" feature. Draw a selection on your screen, then search with Google Lens or extract text via OCR.

![Demo](demo.gif)

## Features

- **Freeform Selection**: Draw circles or any shape around content you want to search
- **Rectangle/Ellipse Mode**: Hold `Ctrl` for rectangles, `Ctrl+Shift` for ellipses
- **Google Lens Integration**: Upload selection and search directly with Google Lens
- **OCR Text Extraction**: Extract text from images using Tesseract
- **Clipboard Support**: Selections are automatically copied to clipboard
- **HiDPI Support**: Works correctly on high-DPI displays
- **Dark Theme**: Modern dark UI using Catppuccin-inspired colors

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
```

**Fedora:**
```bash
sudo dnf install python3 python3-pillow python3-gobject gtk3 grim wl-clipboard
# Optional for OCR:
sudo dnf install tesseract tesseract-langpack-eng python3-pytesseract
```

**Ubuntu/Debian (22.04+ with Wayland):**
```bash
sudo apt install python3 python3-pil python3-gi gir1.2-gtk-3.0 grim wl-clipboard
# Optional for OCR:
sudo apt install tesseract-ocr tesseract-ocr-eng python3-pytesseract
```

#### Run

```bash
git clone https://github.com/YOUR_USERNAME/circle-to-search.git
cd circle-to-search
chmod +x circle-to-search.py
./circle-to-search.py
```

## Usage

1. **Launch** the application (bind to a keyboard shortcut for best experience)
2. **Draw** a circle/shape around the area you want to search
3. **Choose** an action:
   - **Search (Direct)**: Uploads to imgur and opens Google Lens automatically
   - **Google Lens**: Opens Google Lens (paste image manually)
   - **Extract Text (OCR)**: Extracts text using Tesseract

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl` + drag | Draw rectangle |
| `Ctrl+Shift` + drag | Draw ellipse |
| `Escape` | Cancel |

### Recommended: Bind to Keyboard Shortcut

For the best experience, bind the script to a keyboard shortcut in your compositor:

**Hyprland** (`~/.config/hyprland/hyprland.conf`):
```
bind = $mainMod, S, exec, /path/to/circle-to-search.py
```

**Sway** (`~/.config/sway/config`):
```
bindsym $mod+Shift+s exec /path/to/circle-to-search.py
```

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

## How It Works

1. Takes a fullscreen screenshot using `grim`
2. Displays an overlay where you can draw your selection
3. Crops the selected region
4. Copies the selection to clipboard via `wl-copy`
5. Presents options: Google Lens search or OCR text extraction

## License

MIT License - See [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.
