#!/usr/bin/env python3
"""
Circle to Search - Wayland Edition
Draw to select, then show GTK preview dialog
"""

import subprocess
import tempfile
import os
import sys
import urllib.parse

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from PIL import Image

# OCR support (optional)
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


class CircleOverlay(Gtk.Window):
    """Fullscreen overlay for drawing selection"""
    def __init__(self, screenshot_path, callback):
        super().__init__(title="Circle to Search")

        self.screenshot_path = screenshot_path
        self.callback = callback
        self.points = []
        self.drawing = False
        self.selection_made = False
        self.ctrl_held = False
        self.shift_held = False
        self.start_point = None
        self.end_point = None
        self.pixbuf = GdkPixbuf.Pixbuf.new_from_file(screenshot_path)

        # Get screen dimensions
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        self.scale_factor = monitor.get_scale_factor()

        self.screen_width = geometry.width
        self.screen_height = geometry.height

        # Make window fullscreen and transparent
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_default_size(self.screen_width, self.screen_height)
        self.fullscreen()

        # Set up transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        # Create drawing area
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_size_request(self.screen_width, self.screen_height)
        self.add(self.drawing_area)

        # Connect signals
        self.drawing_area.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("key-press-event", self.on_key_press)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.KEY_PRESS_MASK |
            Gdk.EventMask.KEY_RELEASE_MASK
        )
        self.connect("key-release-event", self.on_key_release)

    def on_draw(self, widget, cr):
        # Draw the screenshot as background, scaled to screen size
        # The screenshot is at native resolution, but we display at screen resolution
        cr.scale(1.0 / self.scale_factor, 1.0 / self.scale_factor)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        cr.scale(self.scale_factor, self.scale_factor)  # Reset scale for drawing

        # Semi-transparent overlay
        cr.set_source_rgba(0, 0, 0, 0.3)
        cr.paint()

        # Draw the selection
        cr.set_source_rgba(0.2, 0.6, 1.0, 0.8)
        cr.set_line_width(4)

        if self.ctrl_held and self.start_point and self.end_point:
            x1, y1 = self.start_point
            x2, y2 = self.end_point

            if self.shift_held:
                # Draw perfect ellipse (Ctrl+Shift)
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                rx = abs(x2 - x1) / 2
                ry = abs(y2 - y1) / 2

                cr.save()
                cr.translate(cx, cy)
                if rx > 0 and ry > 0:
                    cr.scale(rx, ry)
                    cr.arc(0, 0, 1, 0, 2 * 3.14159)
                cr.restore()
                cr.stroke()
            else:
                # Draw rectangle (Ctrl only)
                cr.rectangle(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
                cr.stroke()

            # Draw corner markers
            cr.set_source_rgba(0.2, 0.6, 1.0, 1.0)
            for px, py in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
                cr.arc(px, py, 5, 0, 2 * 3.14159)
                cr.fill()

        elif len(self.points) > 1:
            # Draw freeform path
            cr.move_to(self.points[0][0], self.points[0][1])
            for point in self.points[1:]:
                cr.line_to(point[0], point[1])
            cr.stroke()

            # Draw dots
            cr.set_source_rgba(0.2, 0.6, 1.0, 1.0)
            for point in self.points[::5]:
                cr.arc(point[0], point[1], 3, 0, 2 * 3.14159)
                cr.fill()

        # Show help text
        if not self.drawing and len(self.points) == 0 and not self.start_point:
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.select_font_face("Sans", 0, 1)
            cr.set_font_size(24)

            text = "Draw a circle around the area to search"
            extents = cr.text_extents(text)
            x = (self.screen_width - extents.width) / 2
            y = self.screen_height / 2 - 50
            cr.move_to(x, y)
            cr.show_text(text)

            cr.set_font_size(16)
            text2 = "CTRL = rectangle | CTRL+SHIFT = ellipse | ESC = cancel"
            extents2 = cr.text_extents(text2)
            x2 = (self.screen_width - extents2.width) / 2
            cr.move_to(x2, y + 40)
            cr.show_text(text2)

        return False

    def on_button_press(self, widget, event):
        if event.button == 1:
            self.drawing = True
            self.start_point = (event.x, event.y)
            self.end_point = (event.x, event.y)
            self.points = [(event.x, event.y)]
            self.ctrl_held = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
            self.shift_held = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        return True

    def on_motion(self, widget, event):
        if self.drawing:
            self.end_point = (event.x, event.y)
            if not self.ctrl_held:
                self.points.append((event.x, event.y))
            self.drawing_area.queue_draw()
        return True

    def on_button_release(self, widget, event):
        if event.button == 1 and self.drawing:
            self.drawing = False
            if self.ctrl_held and self.start_point and self.end_point:
                # Shape mode - generate points from bounding box
                self.process_selection()
            elif len(self.points) > 10:
                self.process_selection()
            else:
                self.points = []
                self.start_point = None
                self.end_point = None
                self.drawing_area.queue_draw()
        return True

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.callback(None)
            self.destroy()
            Gtk.main_quit()
        elif event.keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.ctrl_held = True
            self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            self.shift_held = True
            self.drawing_area.queue_draw()
        return True

    def on_key_release(self, widget, event):
        if event.keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.ctrl_held = False
            self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            self.shift_held = False
            self.drawing_area.queue_draw()
        return True

    def get_bounding_box(self):
        # Use shape bounds if in ctrl mode
        if self.ctrl_held and self.start_point and self.end_point:
            x1 = min(self.start_point[0], self.end_point[0])
            y1 = min(self.start_point[1], self.end_point[1])
            x2 = max(self.start_point[0], self.end_point[0])
            y2 = max(self.start_point[1], self.end_point[1])
            padding = 5
            x1 = max(0, x1 - padding)
            y1 = max(0, y1 - padding)
            x2 = min(self.screen_width, x2 + padding)
            y2 = min(self.screen_height, y2 + padding)
            return (int(x1), int(y1), int(x2), int(y2))

        if not self.points:
            return None

        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]

        padding = 10
        x1 = max(0, min(xs) - padding)
        y1 = max(0, min(ys) - padding)
        x2 = min(self.screen_width, max(xs) + padding)
        y2 = min(self.screen_height, max(ys) + padding)

        return (int(x1), int(y1), int(x2), int(y2))

    def process_selection(self):
        if self.selection_made:
            return
        self.selection_made = True

        bbox = self.get_bounding_box()
        if not bbox:
            self.callback(None)
            self.destroy()
            Gtk.main_quit()
            return

        x1, y1, x2, y2 = bbox

        # Account for HiDPI scaling
        x1_scaled = int(x1 * self.scale_factor)
        y1_scaled = int(y1 * self.scale_factor)
        x2_scaled = int(x2 * self.scale_factor)
        y2_scaled = int(y2 * self.scale_factor)

        width = x2 - x1
        height = y2 - y1

        if width < 20 or height < 20:
            self.callback(None)
            self.destroy()
            Gtk.main_quit()
            return

        # Crop the region
        img = Image.open(self.screenshot_path)
        cropped = img.crop((x1_scaled, y1_scaled, x2_scaled, y2_scaled))

        # Resize if too large to avoid memory issues
        max_crop_size = 2000
        if cropped.width > max_crop_size or cropped.height > max_crop_size:
            ratio = min(max_crop_size / cropped.width, max_crop_size / cropped.height)
            new_size = (int(cropped.width * ratio), int(cropped.height * ratio))
            cropped = cropped.resize(new_size, Image.LANCZOS)

        # Save cropped image
        temp_dir = tempfile.gettempdir()
        cropped_path = os.path.join(temp_dir, "circle_search_crop.png")
        cropped.save(cropped_path, "PNG", optimize=True)

        # Return result via callback
        self.callback(cropped_path)
        self.destroy()
        Gtk.main_quit()


class ImagePreviewDialog(Gtk.Window):
    """Dark themed dialog showing image preview with options"""
    def __init__(self, image_path):
        super().__init__(title="Circle to Search")
        self.image_path = image_path
        self.result = None

        self.set_default_size(500, 450)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)
        self.set_resizable(True)

        # Dark theme
        self.apply_dark_theme()

        # Main container
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        self.add(vbox)

        # Label
        label = Gtk.Label(label="Selection Preview")
        label.get_style_context().add_class("title")
        vbox.pack_start(label, False, False, 0)

        # Image preview in frame
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(280)

        # Load and display the cropped image (scale down for preview to avoid memory issues)
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_path)
        max_size = 450
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        # Always scale to fit preview, even for smaller images
        if width > max_size or height > max_size:
            scale = min(max_size / width, max_size / height)
            pixbuf = pixbuf.scale_simple(int(width * scale), int(height * scale), GdkPixbuf.InterpType.NEAREST)

        image = Gtk.Image.new_from_pixbuf(pixbuf)
        scrolled.add(image)
        frame.add(scrolled)
        vbox.pack_start(frame, True, True, 0)

        # Buttons row 1 - Image search
        hbox1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox1.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(hbox1, False, False, 4)

        btn_direct = Gtk.Button(label="Search (Direct)")
        btn_direct.get_style_context().add_class("suggested-action")
        btn_direct.connect("clicked", lambda b: self.set_result("tineye"))
        btn_direct.set_tooltip_text("Upload to imgur and open Google Lens with URL")
        hbox1.pack_start(btn_direct, False, False, 0)

        btn_lens = Gtk.Button(label="Google Lens")
        btn_lens.connect("clicked", lambda b: self.set_result("lens"))
        btn_lens.set_tooltip_text("Open Google Lens (paste image manually)")
        hbox1.pack_start(btn_lens, False, False, 0)

        # Buttons row 2 - OCR and Cancel
        hbox2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox2.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(hbox2, False, False, 4)

        btn_ocr = Gtk.Button(label="Extract Text (OCR)")
        btn_ocr.connect("clicked", lambda b: self.set_result("ocr"))
        if not OCR_AVAILABLE:
            btn_ocr.set_sensitive(False)
            btn_ocr.set_tooltip_text("Install: sudo pacman -S python-pytesseract tesseract tesseract-data-eng")
        hbox2.pack_start(btn_ocr, False, False, 0)

        btn_close = Gtk.Button(label="Cancel")
        btn_close.connect("clicked", lambda b: self.set_result(None))
        hbox2.pack_start(btn_close, False, False, 0)

        self.connect("key-press-event", self.on_key_press)
        self.connect("delete-event", lambda w, e: self.set_result(None) or False)

    def apply_dark_theme(self):
        css = b"""
        window {
            background-color: #1e1e2e;
        }
        label {
            color: #cdd6f4;
        }
        label.title {
            font-size: 16px;
            font-weight: bold;
            color: #cdd6f4;
        }
        button {
            background: #313244;
            color: #cdd6f4;
            border: 1px solid #45475a;
            padding: 8px 16px;
            border-radius: 6px;
        }
        button:hover {
            background: #45475a;
        }
        button.suggested-action {
            background: #89b4fa;
            color: #1e1e2e;
        }
        button.suggested-action:hover {
            background: #b4befe;
        }
        frame {
            background: #181825;
            border: 1px solid #313244;
            border-radius: 8px;
        }
        scrolledwindow {
            background: #181825;
        }
        """
        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.set_result(None)
        return False

    def set_result(self, result):
        self.result = result
        Gtk.main_quit()


class TextResultDialog(Gtk.Window):
    """Dark themed dialog showing OCR results"""
    def __init__(self, text):
        super().__init__(title="Extracted Text")
        self.text = text
        self.result = None
        self.final_text = text

        self.set_default_size(500, 350)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        self.add(vbox)

        label = Gtk.Label(label="Extracted Text (copied to clipboard)")
        label.get_style_context().add_class("title")
        vbox.pack_start(label, False, False, 0)

        # Scrolled text view
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(180)

        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_editable(True)
        self.textview.get_buffer().set_text(text)
        scrolled.add(self.textview)
        frame.add(scrolled)
        vbox.pack_start(frame, True, True, 0)

        # Buttons
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(hbox, False, False, 8)

        btn_search = Gtk.Button(label="Search Google")
        btn_search.get_style_context().add_class("suggested-action")
        btn_search.connect("clicked", lambda b: self.set_result("search"))
        hbox.pack_start(btn_search, False, False, 0)

        btn_copy = Gtk.Button(label="Copy & Close")
        btn_copy.connect("clicked", lambda b: self.set_result("copy"))
        hbox.pack_start(btn_copy, False, False, 0)

        btn_close = Gtk.Button(label="Close")
        btn_close.connect("clicked", lambda b: self.set_result(None))
        hbox.pack_start(btn_close, False, False, 0)

        self.connect("key-press-event", self.on_key_press)

    def get_text(self):
        buffer = self.textview.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False)

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.set_result(None)
        return False

    def set_result(self, result):
        self.final_text = self.get_text()
        self.result = result
        Gtk.main_quit()


def take_screenshot():
    temp_dir = tempfile.gettempdir()
    screenshot_path = os.path.join(temp_dir, "circle_search_screenshot.png")
    result = subprocess.run(["grim", screenshot_path], capture_output=True)
    if result.returncode != 0:
        return None
    return screenshot_path


def copy_to_clipboard_image(path):
    with open(path, 'rb') as f:
        subprocess.run(["wl-copy", "-t", "image/png"], stdin=f)


def copy_to_clipboard_text(text):
    proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, text=True)
    proc.communicate(input=text)


def main():
    # Take screenshot
    screenshot_path = take_screenshot()
    if not screenshot_path:
        subprocess.run(["notify-send", "Error", "Failed to take screenshot"])
        sys.exit(1)

    # Phase 1: Circle selection
    crop_path = [None]  # Use list to allow modification in callback

    def on_selection(path):
        crop_path[0] = path

    overlay = CircleOverlay(screenshot_path, on_selection)
    overlay.show_all()
    GLib.timeout_add(100, lambda: overlay.present())
    Gtk.main()

    # Cleanup screenshot
    try:
        os.remove(screenshot_path)
    except:
        pass

    if not crop_path[0]:
        sys.exit(0)

    # Copy image to clipboard
    copy_to_clipboard_image(crop_path[0])

    # Save to persistent location
    persistent_path = "/tmp/circle_search_upload.png"
    subprocess.run(["cp", crop_path[0], persistent_path], capture_output=True)

    # Phase 2: Show preview dialog
    dialog = ImagePreviewDialog(crop_path[0])
    dialog.show_all()
    Gtk.main()

    choice = dialog.result
    dialog.destroy()

    if choice == "tineye":
        # Upload in background script to avoid freezing
        import shutil
        shutil.copy(crop_path[0], persistent_path)

        # Run upload as separate process
        upload_script = f'''
import subprocess, json, urllib.parse
subprocess.run(["notify-send", "-t", "5000", "-i", "image-loading", "Circle to Search", "Uploading image..."])
try:
    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        "-H", "Authorization: Client-ID 546c25a59c58ad7",
        "-F", "image=@{persistent_path}",
        "https://api.imgur.com/3/image"
    ], capture_output=True, text=True, timeout=60)
    response = json.loads(result.stdout) if result.stdout else {{}}
    if response.get("success") and response.get("data", {{}}).get("link"):
        image_url = response["data"]["link"]
        lens_url = f"https://lens.google.com/uploadbyurl?url={{urllib.parse.quote(image_url)}}"
        subprocess.run(["notify-send", "-t", "2000", "-i", "emblem-ok", "Circle to Search", "Opening Google Lens..."])
        subprocess.Popen(["xdg-open", lens_url])
    else:
        subprocess.run(["notify-send", "-i", "dialog-warning", "Upload Failed", "File path copied - paste manually"])
        subprocess.run(["wl-copy", "{persistent_path}"])
        subprocess.Popen(["xdg-open", "https://lens.google.com/"])
except Exception as e:
    subprocess.run(["notify-send", "-i", "dialog-error", "Upload Error", str(e)])
    subprocess.run(["wl-copy", "{persistent_path}"])
    subprocess.Popen(["xdg-open", "https://lens.google.com/"])
'''
        subprocess.Popen(["python3", "-c", upload_script],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True)

    elif choice == "lens":
        # Copy file path and open browser
        subprocess.run(["wl-copy", persistent_path])
        subprocess.Popen(["xdg-open", "https://lens.google.com/"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    elif choice == "ocr":
        try:
            img = Image.open(crop_path[0])
            text = pytesseract.image_to_string(img).strip()

            if text:
                copy_to_clipboard_text(text)

                # Show text dialog
                text_dialog = TextResultDialog(text)
                text_dialog.show_all()
                Gtk.main()

                text_result = text_dialog.result
                final_text = text_dialog.final_text
                text_dialog.destroy()

                if text_result == "search":
                    search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(final_text)}"
                    subprocess.Popen(["xdg-open", search_url],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif text_result == "copy":
                    copy_to_clipboard_text(final_text)
            else:
                subprocess.run(["notify-send", "No Text Found",
                               "OCR could not detect any text"])
        except Exception as e:
            subprocess.run(["notify-send", "OCR Error", str(e)])

    # Cleanup
    try:
        os.remove(crop_path[0])
    except:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
