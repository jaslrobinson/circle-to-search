#!/usr/bin/env python3
"""
Circle to Search - Wayland Edition
Draw to select, then show GTK preview dialog

Modes:
  --live     Use layer-shell for live screen overlay (Hyprland/Sway only)
  --static   Use screenshot-based overlay (default, works everywhere)
"""

import subprocess
import tempfile
import os
import sys
import argparse
import urllib.parse

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from PIL import Image

# Layer shell support (optional - for live mode)
LAYER_SHELL_AVAILABLE = False
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    LAYER_SHELL_AVAILABLE = True
except (ValueError, ImportError):
    pass

# OCR support (optional)
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


class LiveOverlay(Gtk.Window):
    """Layer-shell based overlay for live screen selection (Hyprland/Sway only)"""
    def __init__(self, callback):
        super().__init__(title="Circle to Search")

        self.callback = callback
        self.points = []
        self.drawing = False
        self.selection_made = False
        self.ctrl_held = False
        self.shift_held = False
        self.start_point = None
        self.end_point = None

        # Get screen dimensions
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        self.scale_factor = monitor.get_scale_factor()

        self.screen_width = geometry.width
        self.screen_height = geometry.height

        # Initialize layer shell
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_exclusive_zone(self, -1)  # Cover entire screen
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)

        # Make window transparent
        self.set_decorated(False)
        self.set_app_paintable(True)

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
        import math

        # If in capture mode, draw nothing (fully transparent)
        if getattr(self, '_capture_mode', False):
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            return False

        # Transparent background - live desktop shows through!
        cr.set_source_rgba(0.02, 0.02, 0.08, 0.4)
        cr.paint()

        # Gradient colors for the selection (purple -> pink -> cyan)
        def draw_glow_stroke(path_func, line_width=4):
            for glow_size, alpha in [(12, 0.15), (8, 0.25), (5, 0.4)]:
                cr.set_line_width(line_width + glow_size)
                cr.set_source_rgba(0.55, 0.23, 0.93, alpha)
                path_func()
                cr.stroke()

            cr.set_line_width(line_width)
            cr.set_source_rgba(0.66, 0.33, 0.97, 1.0)
            path_func()
            cr.stroke()

            cr.set_line_width(line_width - 2)
            cr.set_source_rgba(0.93, 0.47, 0.86, 0.6)
            path_func()
            cr.stroke()

        if self.ctrl_held and self.start_point and self.end_point:
            x1, y1 = self.start_point
            x2, y2 = self.end_point

            if self.shift_held:
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                rx = abs(x2 - x1) / 2
                ry = abs(y2 - y1) / 2

                def draw_ellipse():
                    cr.save()
                    cr.translate(cx, cy)
                    if rx > 0 and ry > 0:
                        cr.scale(rx, ry)
                        cr.arc(0, 0, 1, 0, 2 * math.pi)
                    cr.restore()

                draw_glow_stroke(draw_ellipse)
            else:
                def draw_rect():
                    cr.rectangle(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

                draw_glow_stroke(draw_rect)

            for px, py in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
                cr.set_source_rgba(0.55, 0.23, 0.93, 0.4)
                cr.arc(px, py, 10, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(0.93, 0.47, 0.86, 1.0)
                cr.arc(px, py, 5, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 0.8)
                cr.arc(px, py, 2, 0, 2 * math.pi)
                cr.fill()

        elif len(self.points) > 1:
            def draw_path():
                cr.move_to(self.points[0][0], self.points[0][1])
                for point in self.points[1:]:
                    cr.line_to(point[0], point[1])

            draw_glow_stroke(draw_path, line_width=5)

            for i, point in enumerate(self.points[::8]):
                t = i / max(1, len(self.points[::8]) - 1)
                r = 0.55 + 0.38 * t
                g = 0.23 + 0.54 * t
                b = 0.93 - 0.13 * t

                cr.set_source_rgba(r, g, b, 0.4)
                cr.arc(point[0], point[1], 8, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(r, g, b, 1.0)
                cr.arc(point[0], point[1], 4, 0, 2 * math.pi)
                cr.fill()

        # Show help text
        if not self.drawing and len(self.points) == 0 and not self.start_point:
            cr.select_font_face("Sans", 0, 1)

            text = "Draw a circle around the area to search"
            cr.set_font_size(28)
            extents = cr.text_extents(text)
            x = (self.screen_width - extents.width) / 2
            y = self.screen_height / 2 - 50

            cr.set_source_rgba(0.55, 0.23, 0.93, 0.5)
            for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
                cr.move_to(x + dx, y + dy)
                cr.show_text(text)

            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.move_to(x, y)
            cr.show_text(text)

            cr.set_font_size(16)
            text2 = "LIVE MODE  •  CTRL = rectangle  •  CTRL+SHIFT = ellipse  •  ESC = cancel"
            extents2 = cr.text_extents(text2)
            x2 = (self.screen_width - extents2.width) / 2
            cr.set_source_rgba(0.5, 0.93, 0.5, 0.9)
            cr.move_to(x2, y + 45)
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
        width = x2 - x1
        height = y2 - y1

        if width < 20 or height < 20:
            self.callback(None)
            self.destroy()
            Gtk.main_quit()
            return

        # Clear the drawing and make overlay fully transparent before capture
        self.points = []
        self.start_point = None
        self.end_point = None
        self._capture_mode = True  # Flag to draw nothing
        self.drawing_area.queue_draw()

        # Process the redraw
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        # Store capture params
        self._capture_params = (x1, y1, width, height)
        self._callback = self.callback

        # Small delay then capture
        GLib.timeout_add(50, self._do_capture)

    def _do_capture(self):
        x, y, width, height = self._capture_params

        # Now hide and take screenshot
        self.hide()

        # Process hide
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        import time
        time.sleep(0.1)

        # Take screenshot with unique filename to avoid caching issues
        import uuid
        temp_dir = tempfile.gettempdir()
        cropped_path = os.path.join(temp_dir, f"circle_search_crop_{uuid.uuid4().hex[:8]}.png")
        geometry = f"{int(x)},{int(y)} {int(width)}x{int(height)}"

        result = subprocess.run(
            ["grim", "-g", geometry, cropped_path],
            capture_output=True
        )

        if result.returncode == 0:
            self._callback(cropped_path)
        else:
            self._callback(None)

        self.destroy()
        Gtk.main_quit()
        return False


class CircleOverlay(Gtk.Window):
    """Fullscreen overlay for drawing selection (screenshot-based)"""
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
        import math

        # Draw the screenshot as background, scaled to fit screen
        pixbuf_width = self.pixbuf.get_width()
        pixbuf_height = self.pixbuf.get_height()

        scale_x = self.screen_width / pixbuf_width
        scale_y = self.screen_height / pixbuf_height

        cr.scale(scale_x, scale_y)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        cr.scale(1.0 / scale_x, 1.0 / scale_y)

        # Semi-transparent dark overlay with subtle gradient
        cr.set_source_rgba(0.02, 0.02, 0.08, 0.4)
        cr.paint()

        # Gradient colors for the selection (purple -> pink -> cyan)
        def draw_glow_stroke(path_func, line_width=4):
            # Outer glow layers
            for glow_size, alpha in [(12, 0.15), (8, 0.25), (5, 0.4)]:
                cr.set_line_width(line_width + glow_size)
                cr.set_source_rgba(0.55, 0.23, 0.93, alpha)  # Purple glow
                path_func()
                cr.stroke()

            # Main gradient stroke
            cr.set_line_width(line_width)
            cr.set_source_rgba(0.66, 0.33, 0.97, 1.0)  # Vibrant purple
            path_func()
            cr.stroke()

            # Inner highlight
            cr.set_line_width(line_width - 2)
            cr.set_source_rgba(0.93, 0.47, 0.86, 0.6)  # Pink highlight
            path_func()
            cr.stroke()

        if self.ctrl_held and self.start_point and self.end_point:
            x1, y1 = self.start_point
            x2, y2 = self.end_point

            if self.shift_held:
                # Draw perfect ellipse (Ctrl+Shift)
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                rx = abs(x2 - x1) / 2
                ry = abs(y2 - y1) / 2

                def draw_ellipse():
                    cr.save()
                    cr.translate(cx, cy)
                    if rx > 0 and ry > 0:
                        cr.scale(rx, ry)
                        cr.arc(0, 0, 1, 0, 2 * math.pi)
                    cr.restore()

                draw_glow_stroke(draw_ellipse)
            else:
                # Draw rectangle (Ctrl only)
                def draw_rect():
                    cr.rectangle(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

                draw_glow_stroke(draw_rect)

            # Draw glowing corner markers
            for px, py in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
                # Outer glow
                cr.set_source_rgba(0.55, 0.23, 0.93, 0.4)
                cr.arc(px, py, 10, 0, 2 * math.pi)
                cr.fill()
                # Inner bright circle
                cr.set_source_rgba(0.93, 0.47, 0.86, 1.0)
                cr.arc(px, py, 5, 0, 2 * math.pi)
                cr.fill()
                # Center highlight
                cr.set_source_rgba(1, 1, 1, 0.8)
                cr.arc(px, py, 2, 0, 2 * math.pi)
                cr.fill()

        elif len(self.points) > 1:
            # Draw freeform path with glow
            def draw_path():
                cr.move_to(self.points[0][0], self.points[0][1])
                for point in self.points[1:]:
                    cr.line_to(point[0], point[1])

            draw_glow_stroke(draw_path, line_width=5)

            # Draw gradient dots along the path
            for i, point in enumerate(self.points[::8]):
                t = i / max(1, len(self.points[::8]) - 1)
                # Gradient from purple to pink to cyan
                r = 0.55 + 0.38 * t
                g = 0.23 + 0.54 * t
                b = 0.93 - 0.13 * t

                # Glow
                cr.set_source_rgba(r, g, b, 0.4)
                cr.arc(point[0], point[1], 8, 0, 2 * math.pi)
                cr.fill()
                # Dot
                cr.set_source_rgba(r, g, b, 1.0)
                cr.arc(point[0], point[1], 4, 0, 2 * math.pi)
                cr.fill()

        # Show help text with glow effect
        if not self.drawing and len(self.points) == 0 and not self.start_point:
            cr.select_font_face("Sans", 0, 1)  # Bold

            text = "Draw a circle around the area to search"
            cr.set_font_size(28)
            extents = cr.text_extents(text)
            x = (self.screen_width - extents.width) / 2
            y = self.screen_height / 2 - 50

            # Text glow
            cr.set_source_rgba(0.55, 0.23, 0.93, 0.5)
            for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
                cr.move_to(x + dx, y + dy)
                cr.show_text(text)

            # Main text
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.move_to(x, y)
            cr.show_text(text)

            cr.set_font_size(16)
            text2 = "CTRL = rectangle  •  CTRL+SHIFT = ellipse  •  ESC = cancel"
            extents2 = cr.text_extents(text2)
            x2 = (self.screen_width - extents2.width) / 2
            cr.set_source_rgba(0.8, 0.8, 0.9, 0.8)
            cr.move_to(x2, y + 45)
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

        width = x2 - x1
        height = y2 - y1

        if width < 20 or height < 20:
            self.callback(None)
            self.destroy()
            Gtk.main_quit()
            return

        # Crop the region - scale coordinates from screen to image
        img = Image.open(self.screenshot_path)
        img_width, img_height = img.size

        # Calculate scale from screen coords to image coords
        scale_x = img_width / self.screen_width
        scale_y = img_height / self.screen_height

        x1_scaled = int(x1 * scale_x)
        y1_scaled = int(y1 * scale_y)
        x2_scaled = int(x2 * scale_x)
        y2_scaled = int(y2 * scale_y)

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

        self.set_default_size(520, 500)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)
        self.set_resizable(True)
        self.set_decorated(False)  # Remove window decorations for cleaner look

        # Dark theme
        self.apply_dark_theme()

        # Main container with rounded corners effect
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_box)

        # Custom title bar
        title_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_bar.set_size_request(-1, 40)
        title_bar.get_style_context().add_class("titlebar")

        # Title
        title_label = Gtk.Label(label="  Circle to Search")
        title_label.get_style_context().add_class("window-title")
        title_label.set_halign(Gtk.Align.START)
        title_bar.pack_start(title_label, True, True, 8)

        # Close button
        close_btn = Gtk.Button(label="✕")
        close_btn.get_style_context().add_class("close-button")
        close_btn.connect("clicked", lambda b: self.set_result(None))
        close_btn.set_size_request(40, 40)
        title_bar.pack_end(close_btn, False, False, 0)

        main_box.pack_start(title_bar, False, False, 0)

        # Content container
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(24)
        vbox.set_margin_start(24)
        vbox.set_margin_end(24)
        main_box.pack_start(vbox, True, True, 0)

        # Section label
        label = Gtk.Label(label="Selection Preview")
        label.get_style_context().add_class("title")
        vbox.pack_start(label, False, False, 0)

        # Image preview in frame
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.get_style_context().add_class("image-frame")

        # Load and display the cropped image
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_path)
        max_size = 420
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        if width > max_size or height > max_size:
            scale = min(max_size / width, max_size / height)
            pixbuf = pixbuf.scale_simple(int(width * scale), int(height * scale), GdkPixbuf.InterpType.BILINEAR)

        image = Gtk.Image.new_from_pixbuf(pixbuf)
        image.set_margin_top(12)
        image.set_margin_bottom(12)
        image.set_margin_start(12)
        image.set_margin_end(12)

        # Center image using Box instead of deprecated Alignment
        image_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        image_box.set_halign(Gtk.Align.CENTER)
        image_box.set_valign(Gtk.Align.CENTER)
        image_box.pack_start(image, False, False, 0)
        image_box.set_size_request(450, 280)

        frame.add(image_box)
        vbox.pack_start(frame, True, True, 0)

        # Buttons container
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.pack_start(buttons_box, False, False, 8)

        # Primary action button (full width)
        btn_direct = Gtk.Button(label="🔍  Search with Google Lens")
        btn_direct.get_style_context().add_class("suggested-action")
        btn_direct.get_style_context().add_class("primary-button")
        btn_direct.connect("clicked", lambda b: self.set_result("tineye"))
        btn_direct.set_tooltip_text("Upload to imgur and open Google Lens automatically")
        buttons_box.pack_start(btn_direct, False, False, 0)

        # Secondary buttons row
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_homogeneous(True)
        buttons_box.pack_start(hbox, False, False, 0)

        btn_lens = Gtk.Button(label="📋  Manual Paste")
        btn_lens.connect("clicked", lambda b: self.set_result("lens"))
        btn_lens.set_tooltip_text("Open Google Lens (paste image manually)")
        hbox.pack_start(btn_lens, True, True, 0)

        btn_ocr = Gtk.Button(label="📝  Extract Text")
        btn_ocr.connect("clicked", lambda b: self.set_result("ocr"))
        if not OCR_AVAILABLE:
            btn_ocr.set_sensitive(False)
            btn_ocr.set_tooltip_text("Install: sudo pacman -S python-pytesseract tesseract tesseract-data-eng")
        else:
            btn_ocr.set_tooltip_text("Extract text using OCR")
        hbox.pack_start(btn_ocr, True, True, 0)

        # Make window draggable from title bar
        title_bar.connect("button-press-event", self.on_title_bar_press)

        self.connect("key-press-event", self.on_key_press)
        self.connect("delete-event", lambda w, e: self.set_result(None) or False)

    def on_title_bar_press(self, widget, event):
        if event.button == 1:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return True

    def apply_dark_theme(self):
        css = b"""
        window {
            background: #0d0d1a;
            border-radius: 16px;
        }
        .titlebar {
            background: linear-gradient(90deg, #1a1a2e 0%, #16213e 100%);
            border-bottom: 1px solid rgba(139, 92, 246, 0.2);
        }
        label.window-title {
            color: #a78bfa;
            font-size: 14px;
            font-weight: 600;
            font-family: "Inter", "SF Pro Display", "Segoe UI", sans-serif;
        }
        .close-button {
            background: transparent;
            border: none;
            border-radius: 0;
            color: #64748b;
            font-size: 16px;
            padding: 0;
            box-shadow: none;
        }
        .close-button:hover {
            background: rgba(239, 68, 68, 0.2);
            color: #ef4444;
            box-shadow: none;
        }
        label {
            color: #e2e8f0;
            font-family: "Inter", "SF Pro Display", "Segoe UI", sans-serif;
        }
        label.title {
            font-size: 16px;
            font-weight: 600;
            color: #c4b5fd;
            letter-spacing: 0.3px;
        }
        button {
            background: linear-gradient(135deg, #1e1e3f 0%, #2d2d5a 100%);
            color: #e2e8f0;
            border: 1px solid rgba(139, 92, 246, 0.3);
            padding: 12px 20px;
            border-radius: 10px;
            font-weight: 500;
            font-size: 13px;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3),
                        inset 0 1px 0 rgba(255, 255, 255, 0.1);
        }
        button:hover {
            background: linear-gradient(135deg, #2d2d5a 0%, #3d3d7a 100%);
            border-color: rgba(139, 92, 246, 0.6);
            box-shadow: 0 6px 20px rgba(139, 92, 246, 0.25),
                        inset 0 1px 0 rgba(255, 255, 255, 0.15);
        }
        button:active {
            background: linear-gradient(135deg, #1a1a3a 0%, #2a2a5a 100%);
        }
        button.suggested-action {
            background: linear-gradient(135deg, #7c3aed 0%, #a855f7 50%, #c026d3 100%);
            color: #ffffff;
            border: none;
            font-weight: 600;
            box-shadow: 0 4px 20px rgba(168, 85, 247, 0.4),
                        inset 0 1px 0 rgba(255, 255, 255, 0.2);
        }
        button.suggested-action:hover {
            background: linear-gradient(135deg, #8b5cf6 0%, #c084fc 50%, #d946ef 100%);
            box-shadow: 0 6px 25px rgba(168, 85, 247, 0.5);
        }
        button.primary-button {
            padding: 14px 24px;
            font-size: 14px;
            border-radius: 12px;
        }
        .image-frame {
            background: rgba(10, 10, 25, 0.9);
            border: 1px solid rgba(139, 92, 246, 0.25);
            border-radius: 12px;
            box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.3);
        }
        frame {
            background: rgba(15, 15, 35, 0.8);
            border: 1px solid rgba(139, 92, 246, 0.2);
            border-radius: 12px;
        }
        scrolledwindow {
            background: rgba(15, 15, 35, 0.6);
            border-radius: 10px;
        }
        textview {
            background: rgba(10, 10, 25, 0.9);
            color: #e2e8f0;
            font-family: "JetBrains Mono", "Fira Code", monospace;
        }
        textview text {
            background: transparent;
            color: #e2e8f0;
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

        self.set_default_size(520, 420)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)
        self.set_decorated(False)

        # Apply theme (reuse from ImagePreviewDialog)
        self.apply_dark_theme()

        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_box)

        # Custom title bar
        title_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_bar.set_size_request(-1, 40)
        title_bar.get_style_context().add_class("titlebar")

        title_label = Gtk.Label(label="  Extracted Text")
        title_label.get_style_context().add_class("window-title")
        title_label.set_halign(Gtk.Align.START)
        title_bar.pack_start(title_label, True, True, 8)

        close_btn = Gtk.Button(label="✕")
        close_btn.get_style_context().add_class("close-button")
        close_btn.connect("clicked", lambda b: self.set_result(None))
        close_btn.set_size_request(40, 40)
        title_bar.pack_end(close_btn, False, False, 0)

        main_box.pack_start(title_bar, False, False, 0)

        # Content
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(24)
        vbox.set_margin_start(24)
        vbox.set_margin_end(24)
        main_box.pack_start(vbox, True, True, 0)

        # Status label
        status_label = Gtk.Label(label="✓ Copied to clipboard")
        status_label.get_style_context().add_class("title")
        vbox.pack_start(status_label, False, False, 0)

        # Scrolled text view
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.get_style_context().add_class("image-frame")

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)

        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_editable(True)
        self.textview.set_left_margin(12)
        self.textview.set_right_margin(12)
        self.textview.set_top_margin(12)
        self.textview.set_bottom_margin(12)
        self.textview.get_buffer().set_text(text)
        scrolled.add(self.textview)
        frame.add(scrolled)
        vbox.pack_start(frame, True, True, 0)

        # Buttons
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.pack_start(buttons_box, False, False, 8)

        # Row 1: Search
        btn_search = Gtk.Button(label="🔍  Search Google")
        btn_search.get_style_context().add_class("suggested-action")
        btn_search.get_style_context().add_class("primary-button")
        btn_search.connect("clicked", lambda b: self.set_result("search"))
        buttons_box.pack_start(btn_search, False, False, 0)

        # Row 2: Translate, Calculate, AI Explain
        hbox1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox1.set_homogeneous(True)
        buttons_box.pack_start(hbox1, False, False, 0)

        btn_translate = Gtk.Button(label="🌐  Translate")
        btn_translate.connect("clicked", lambda b: self.set_result("translate"))
        btn_translate.set_tooltip_text("Open Google Translate")
        hbox1.pack_start(btn_translate, True, True, 0)

        btn_calculate = Gtk.Button(label="🧮  Calculate")
        btn_calculate.connect("clicked", lambda b: self.set_result("calculate"))
        btn_calculate.set_tooltip_text("Evaluate as math expression")
        hbox1.pack_start(btn_calculate, True, True, 0)

        btn_ai = Gtk.Button(label="🤖  AI Explain")
        btn_ai.connect("clicked", lambda b: self.set_result("ai_explain"))
        btn_ai.set_tooltip_text("Explain with AI (opens browser)")
        hbox1.pack_start(btn_ai, True, True, 0)

        # Row 3: Copy & Close, Cancel
        hbox2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox2.set_homogeneous(True)
        buttons_box.pack_start(hbox2, False, False, 0)

        btn_copy = Gtk.Button(label="📋  Copy & Close")
        btn_copy.connect("clicked", lambda b: self.set_result("copy"))
        hbox2.pack_start(btn_copy, True, True, 0)

        btn_close = Gtk.Button(label="Cancel")
        btn_close.connect("clicked", lambda b: self.set_result(None))
        hbox2.pack_start(btn_close, True, True, 0)

        title_bar.connect("button-press-event", self.on_title_bar_press)
        self.connect("key-press-event", self.on_key_press)

    def on_title_bar_press(self, widget, event):
        if event.button == 1:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return True

    def apply_dark_theme(self):
        css = b"""
        window {
            background: #0d0d1a;
            border-radius: 16px;
        }
        .titlebar {
            background: linear-gradient(90deg, #1a1a2e 0%, #16213e 100%);
            border-bottom: 1px solid rgba(139, 92, 246, 0.2);
        }
        label.window-title {
            color: #a78bfa;
            font-size: 14px;
            font-weight: 600;
        }
        .close-button {
            background: transparent;
            border: none;
            border-radius: 0;
            color: #64748b;
            font-size: 16px;
            padding: 0;
            box-shadow: none;
        }
        .close-button:hover {
            background: rgba(239, 68, 68, 0.2);
            color: #ef4444;
            box-shadow: none;
        }
        label {
            color: #e2e8f0;
        }
        label.title {
            font-size: 14px;
            font-weight: 500;
            color: #86efac;
        }
        button {
            background: linear-gradient(135deg, #1e1e3f 0%, #2d2d5a 100%);
            color: #e2e8f0;
            border: 1px solid rgba(139, 92, 246, 0.3);
            padding: 12px 20px;
            border-radius: 10px;
            font-weight: 500;
            font-size: 13px;
        }
        button:hover {
            background: linear-gradient(135deg, #2d2d5a 0%, #3d3d7a 100%);
            border-color: rgba(139, 92, 246, 0.6);
        }
        button.suggested-action {
            background: linear-gradient(135deg, #7c3aed 0%, #a855f7 50%, #c026d3 100%);
            color: #ffffff;
            border: none;
            font-weight: 600;
        }
        button.suggested-action:hover {
            background: linear-gradient(135deg, #8b5cf6 0%, #c084fc 50%, #d946ef 100%);
        }
        button.primary-button {
            padding: 14px 24px;
            font-size: 14px;
            border-radius: 12px;
        }
        .image-frame {
            background: rgba(10, 10, 25, 0.9);
            border: 1px solid rgba(139, 92, 246, 0.25);
            border-radius: 12px;
        }
        textview {
            background: transparent;
            color: #e2e8f0;
            font-family: "JetBrains Mono", "Fira Code", monospace;
            font-size: 13px;
        }
        textview text {
            background: transparent;
            color: #e2e8f0;
        }
        """
        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

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
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Circle to Search - Draw to select, search with Google Lens",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  --static   Screenshot-based overlay (default, works everywhere)
  --live     Layer-shell overlay with live screen (Hyprland/Sway only)

Example keybindings for Hyprland:
  bind = $mainMod, S, exec, circle-to-search.py --static
  bind = $mainMod SHIFT, S, exec, circle-to-search.py --live
        """
    )
    parser.add_argument('--live', action='store_true',
                       help='Use layer-shell for live screen overlay (Hyprland/Sway only)')
    parser.add_argument('--static', action='store_true',
                       help='Use screenshot-based overlay (default)')
    args = parser.parse_args()

    use_live_mode = args.live

    # Check if live mode is requested but not available
    if use_live_mode and not LAYER_SHELL_AVAILABLE:
        subprocess.run([
            "notify-send", "-i", "dialog-warning",
            "Live Mode Unavailable",
            "gtk-layer-shell not found. Install with:\nsudo pacman -S gtk-layer-shell\n\nFalling back to static mode."
        ])
        use_live_mode = False

    # Check if we're on a supported compositor for live mode
    if use_live_mode:
        # Check for wlroots-based compositor
        wayland_display = os.environ.get('WAYLAND_DISPLAY', '')
        xdg_session = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
        hyprland = os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')

        is_supported = bool(hyprland) or 'sway' in xdg_session or 'hyprland' in xdg_session

        if not is_supported:
            subprocess.run([
                "notify-send", "-i", "dialog-warning",
                "Live Mode Unavailable",
                f"Live mode requires Hyprland or Sway.\nDetected: {xdg_session or 'unknown'}\n\nFalling back to static mode."
            ])
            use_live_mode = False

    crop_path = [None]

    def on_selection(path):
        crop_path[0] = path

    if use_live_mode:
        # Live mode - no screenshot needed, overlay is transparent
        overlay = LiveOverlay(on_selection)
        overlay.show_all()
        Gtk.main()
    else:
        # Static mode - take screenshot first
        screenshot_path = take_screenshot()
        if not screenshot_path:
            subprocess.run(["notify-send", "Error", "Failed to take screenshot"])
            sys.exit(1)

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
                elif text_result == "translate":
                    translate_url = f"https://translate.google.com/?sl=auto&tl=en&text={urllib.parse.quote_plus(final_text)}"
                    subprocess.Popen(["xdg-open", translate_url],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif text_result == "calculate":
                    try:
                        # Clean up text for calculation
                        expr = final_text.strip()
                        # Replace common text representations
                        expr = expr.replace('×', '*').replace('÷', '/').replace('^', '**')
                        expr = expr.replace('x', '*').replace('X', '*')
                        # Only allow safe characters for eval
                        allowed = set('0123456789+-*/.() ')
                        if all(c in allowed for c in expr):
                            result = eval(expr)
                            copy_to_clipboard_text(str(result))
                            subprocess.run(["notify-send", "Calculate Result",
                                           f"{expr} = {result}\n\nCopied to clipboard"])
                        else:
                            # If complex expression, use Google Calculator
                            calc_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(final_text)}"
                            subprocess.Popen(["xdg-open", calc_url],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except:
                        # Fallback to Google Calculator
                        calc_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(final_text)}"
                        subprocess.Popen(["xdg-open", calc_url],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif text_result == "ai_explain":
                    # Open ChatGPT with the text as a prompt
                    prompt = f"Explain this: {final_text}"
                    ai_url = f"https://chatgpt.com/?q={urllib.parse.quote_plus(prompt)}"
                    subprocess.Popen(["xdg-open", ai_url],
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
