# Circle to Search v1.2.0

## Features

- **Freeform Selection** - Draw circles or any shape around content
- **Rectangle Mode** - Hold `Ctrl` while drawing
- **Ellipse Mode** - Hold `Ctrl+Shift` while drawing
- **Google Lens Integration** - Upload and search automatically
- **OCR Text Extraction** - Extract text using Tesseract
- **Multi-Desktop Support** - KDE Plasma, GNOME, Hyprland, Sway
- **Clipboard Support** - Selections automatically copied
- **HiDPI Support** - Works on high-DPI displays
- **Modern UI** - Dark theme with purple/pink neon glow effects

---

## Selection Modes

### Static Mode (Default)

```
Super + Ctrl + S
```

```bash
./circle-to-search.py --static
```

- Takes a screenshot, displays it as overlay background
- Screen is frozen while you draw
- **Works everywhere**: Hyprland, Sway, GNOME, KDE, etc.
- Most reliable option

---

### Live Mode

```
Super + Ctrl + Shift + S
```

```bash
./circle-to-search.py --live
```

- Transparent overlay on your actual live desktop
- See real-time screen content while drawing
- **Hyprland/Sway only** (wlroots-based compositors)
- Requires `gtk-layer-shell` package
- Falls back to static mode automatically if unsupported

**Note:** Windows may change appearance (opacity/focus) when overlay appears

---

## Requirements

**All modes:**
- Wayland compositor
- Screenshot tool: `grim` (wlroots), `spectacle` (KDE), or GNOME Shell D-Bus (GNOME 42+, built-in)
- `wl-clipboard`

**Live mode (additional):**
- Hyprland or Sway
- `gtk-layer-shell`

**OCR (optional):**
- `tesseract`, `python-pytesseract`
