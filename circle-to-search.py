#!/usr/bin/env python3
"""
Circle to Search - Wayland Edition
Draw to select, then show GTK preview dialog

Modes:
  --live     Use layer-shell for live screen overlay (Hyprland/Sway only)
  --static   Use screenshot-based overlay (default, works everywhere)

Screenshot support:
  - grim: For wlroots-based compositors (Hyprland, Sway, etc.)
  - spectacle: For KDE Plasma
  - GNOME Shell D-Bus API: For GNOME 42+ (native, no extra packages needed)
"""

import subprocess
import tempfile
import os
import sys
import argparse
import urllib.parse
import threading
import json
from pathlib import Path

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

# Numpy for edge detection
import numpy as np


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
        self.alt_held = False
        self.start_point = None
        self.end_point = None

        # Edit mode - for adjusting points after tracing
        self.edit_mode = False
        self.dragging_point_idx = None
        self.hover_point_idx = None
        self.simplified_points = []

        # Swipe detection for adjusting point count
        self.swipe_start_y = None
        self.swipe_accumulated = 0
        self.swipe_threshold = 80
        self.original_contour_points = None

        # Undo history for point movements
        self.point_history = []

        # Connect-the-dots mode
        self.dot_mode = False
        self.dot_points = []

        # Mode selector
        self.mode_selector_active = False
        self.selected_mode = 'freehand'
        self.hovered_button = None

        # Define mode buttons
        self.mode_buttons = [
            {'id': 'dots', 'label': 'Connect Dots', 'desc': 'Click to place points'},
            {'id': 'freehand', 'label': 'Freehand', 'desc': 'Draw freely'},
            {'id': 'rectangle', 'label': 'Rectangle', 'desc': 'Drag box shape'},
        ]
        self.button_rects = []

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
        GtkLayerShell.set_exclusive_zone(self, -1)
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

        # Mode selector UI
        if self.mode_selector_active:
            # Draw mode selector buttons
            button_width = 140
            button_height = 70
            button_spacing = 20
            total_width = len(self.mode_buttons) * button_width + (len(self.mode_buttons) - 1) * button_spacing
            start_x = (self.screen_width - total_width) / 2
            start_y = self.screen_height / 2 - button_height / 2

            self.button_rects = []
            for i, btn in enumerate(self.mode_buttons):
                x = start_x + i * (button_width + button_spacing)
                y = start_y
                is_hovered = self.hovered_button == btn['id']

                # Button background
                if is_hovered:
                    cr.set_source_rgba(0.55, 0.23, 0.93, 0.6)
                else:
                    cr.set_source_rgba(0.1, 0.1, 0.2, 0.8)
                cr.rectangle(x, y, button_width, button_height)
                cr.fill()

                # Button border
                cr.set_line_width(2)
                if is_hovered:
                    cr.set_source_rgba(0.93, 0.47, 0.86, 1.0)
                else:
                    cr.set_source_rgba(0.55, 0.23, 0.93, 0.6)
                cr.rectangle(x, y, button_width, button_height)
                cr.stroke()

                # Button label
                cr.select_font_face("Sans", 0, 1)
                cr.set_font_size(14)
                cr.set_source_rgba(1, 1, 1, 0.95)
                label_extents = cr.text_extents(btn['label'])
                cr.move_to(x + (button_width - label_extents.width) / 2, y + 28)
                cr.show_text(btn['label'])

                # Button description
                cr.set_font_size(11)
                cr.set_source_rgba(0.8, 0.8, 0.9, 0.7)
                desc_extents = cr.text_extents(btn['desc'])
                cr.move_to(x + (button_width - desc_extents.width) / 2, y + 48)
                cr.show_text(btn['desc'])

                self.button_rects.append({'id': btn['id'], 'x': x, 'y': y, 'w': button_width, 'h': button_height})

            # Title
            cr.select_font_face("Sans", 0, 1)
            cr.set_font_size(24)
            title = "Select Mode"
            title_extents = cr.text_extents(title)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.move_to((self.screen_width - title_extents.width) / 2, start_y - 40)
            cr.show_text(title)

            # Instructions
            cr.set_font_size(14)
            inst = "LIVE MODE  •  M = close  •  ESC = cancel"
            inst_extents = cr.text_extents(inst)
            cr.set_source_rgba(0.5, 0.93, 0.5, 0.8)
            cr.move_to((self.screen_width - inst_extents.width) / 2, start_y + button_height + 40)
            cr.show_text(inst)

            return False

        # Draw rectangle if in ctrl mode
        if self.ctrl_held and self.start_point and self.end_point:
            x1, y1 = self.start_point
            x2, y2 = self.end_point

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

        # Draw dot mode points
        elif self.dot_mode and self.dot_points and not self.edit_mode:
            # Draw connecting lines
            if len(self.dot_points) > 1:
                def draw_dot_lines():
                    cr.move_to(self.dot_points[0][0], self.dot_points[0][1])
                    for point in self.dot_points[1:]:
                        cr.line_to(point[0], point[1])
                draw_glow_stroke(draw_dot_lines, line_width=3)

            # Draw numbered dots - scale size based on point count
            num_dots = len(self.dot_points)
            # Scale from 18 (at 3 points) down to 8 (at 50+ points)
            outer_radius = max(8, 18 - (num_dots - 3) * 10 / 47)
            inner_radius = outer_radius * 0.67
            font_size = max(8, 12 - (num_dots - 3) * 4 / 47)

            for i, (px, py) in enumerate(self.dot_points):
                cr.set_source_rgba(0.55, 0.23, 0.93, 0.6)
                cr.arc(px, py, outer_radius, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(0.93, 0.47, 0.86, 1.0)
                cr.arc(px, py, inner_radius, 0, 2 * math.pi)
                cr.fill()
                # Number
                cr.select_font_face("Sans", 0, 1)
                cr.set_font_size(font_size)
                cr.set_source_rgba(1, 1, 1, 0.95)
                num_str = str(i + 1)
                extents = cr.text_extents(num_str)
                cr.move_to(px - extents.width / 2, py + extents.height / 2 - 1)
                cr.show_text(num_str)

        # Draw freehand points
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

        # Edit mode - show control points
        if self.edit_mode and self.simplified_points:
            # Draw the shape outline
            if len(self.simplified_points) > 1:
                def draw_edit_path():
                    cr.move_to(self.simplified_points[0][0], self.simplified_points[0][1])
                    for point in self.simplified_points[1:]:
                        cr.line_to(point[0], point[1])
                    cr.close_path()
                draw_glow_stroke(draw_edit_path, line_width=3)

            # Draw control points - scale size based on point count
            num_points = len(self.simplified_points)
            # Scale from 10 (at 8 points) down to 4 (at 200 points)
            base_size = max(4, 10 - (num_points - 8) * 6 / 192)
            hover_size = base_size * 1.4
            inner_size = base_size * 0.4
            glow_size = base_size * 1.4  # Outer glow scales too

            for i, (px, py) in enumerate(self.simplified_points):
                is_hover = (i == self.hover_point_idx)
                is_dragging = (i == self.dragging_point_idx)
                current_size = hover_size if (is_hover or is_dragging) else base_size
                current_inner = inner_size * 1.5 if (is_hover or is_dragging) else inner_size
                current_glow = glow_size * 1.3 if (is_hover or is_dragging) else glow_size

                # Outer glow
                cr.set_source_rgba(0.55, 0.23, 0.93, 0.4)
                cr.arc(px, py, current_glow, 0, 2 * math.pi)
                cr.fill()
                # Main circle
                if is_dragging:
                    cr.set_source_rgba(0.93, 0.8, 0.2, 1.0)
                elif is_hover:
                    cr.set_source_rgba(0.93, 0.47, 0.86, 1.0)
                else:
                    cr.set_source_rgba(0.66, 0.33, 0.97, 1.0)
                cr.arc(px, py, current_size, 0, 2 * math.pi)
                cr.fill()
                # Inner circle
                cr.set_source_rgba(1, 1, 1, 0.9)
                cr.arc(px, py, current_inner, 0, 2 * math.pi)
                cr.fill()

            # Show edit mode instructions
            cr.select_font_face("Sans", 0, 1)
            cr.set_font_size(18)
            text = f"Drag points  •  ↑↓ = ±points ({len(self.simplified_points)})  •  ENTER = confirm"
            extents = cr.text_extents(text)
            x = (self.screen_width - extents.width) / 2
            y = 50

            cr.set_source_rgba(0, 0, 0, 0.7)
            cr.rectangle(x - 20, y - 25, extents.width + 40, 40)
            cr.fill()

            cr.set_source_rgba(0.4, 1, 0.6, 1)
            cr.move_to(x, y)
            cr.show_text(text)

        # Show help for dot mode
        elif self.dot_mode and not self.dot_points and not self.edit_mode:
            cr.select_font_face("Sans", 0, 1)
            text = "Click to place points"
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
            text2 = "LIVE MODE  •  ENTER = finish  •  BACKSPACE = undo  •  M = modes  •  ESC = cancel"
            extents2 = cr.text_extents(text2)
            x2 = (self.screen_width - extents2.width) / 2
            cr.set_source_rgba(0.5, 0.93, 0.5, 0.9)
            cr.move_to(x2, y + 45)
            cr.show_text(text2)

        # Show help for other modes
        elif self.selected_mode and not self.edit_mode and not self.drawing and len(self.points) == 0:
            cr.select_font_face("Sans", 0, 1)

            if self.selected_mode == 'freehand':
                text = "Draw around the object"
                text2 = "LIVE MODE  •  Hold mouse and draw  •  ENTER = full image  •  M = modes  •  ESC = cancel"
            elif self.selected_mode == 'rectangle':
                text = "Drag to draw a rectangle"
                text2 = "LIVE MODE  •  Click and drag  •  ESC = cancel"
            else:
                text = ""
                text2 = ""

            if text:
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
                extents2 = cr.text_extents(text2)
                x2 = (self.screen_width - extents2.width) / 2
                cr.set_source_rgba(0.5, 0.93, 0.5, 0.9)
                cr.move_to(x2, y + 45)
                cr.show_text(text2)

        return False

    def on_button_press(self, widget, event):
        # Mode selector: check if clicking on a button
        if self.mode_selector_active and event.button == 1:
            if hasattr(self, 'button_rects'):
                for btn in self.button_rects:
                    if (btn['x'] <= event.x <= btn['x'] + btn['w'] and
                        btn['y'] <= event.y <= btn['y'] + btn['h']):
                        self.select_mode(btn['id'])
                        return True
            return True

        # Connect-the-dots mode: click to place points
        if self.dot_mode and not self.edit_mode:
            if event.button == 1:
                self.dot_points.append((event.x, event.y))
                self.drawing_area.queue_draw()
                return True
            elif event.button == 3:
                if self.dot_points:
                    self.dot_points.pop()
                    self.drawing_area.queue_draw()
                return True

        if event.button == 1:
            if self.edit_mode:
                # Check if clicking on a control point
                for i, (px, py) in enumerate(self.simplified_points):
                    dist = ((event.x - px) ** 2 + (event.y - py) ** 2) ** 0.5
                    if dist < 20:
                        self.dragging_point_idx = i
                        self.point_history.append((i, (px, py)))
                        self.drawing_area.queue_draw()
                        return True
                # Start swipe detection
                self.swipe_start_y = event.y
                self.swipe_accumulated = 0
            else:
                self.drawing = True
                self.start_point = (event.x, event.y)
                self.end_point = (event.x, event.y)

                if self.selected_mode is None:
                    self.ctrl_held = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
                    self.shift_held = bool(event.state & Gdk.ModifierType.SHIFT_MASK)

                self.points = [(event.x, event.y)]
        return True

    def select_mode(self, mode_id):
        """Handle mode button selection"""
        self.mode_selector_active = False
        self.selected_mode = mode_id

        if mode_id == 'dots':
            self.dot_mode = True
            self.ctrl_held = False
            self.shift_held = False
        elif mode_id == 'freehand':
            self.dot_mode = False
            self.ctrl_held = False
            self.shift_held = False
        elif mode_id == 'rectangle':
            self.dot_mode = False
            self.ctrl_held = True
            self.shift_held = False

        self.drawing_area.queue_draw()

    def on_motion(self, widget, event):
        # Mode selector hover detection
        if self.mode_selector_active:
            old_hover = self.hovered_button
            self.hovered_button = None
            if hasattr(self, 'button_rects'):
                for btn in self.button_rects:
                    if (btn['x'] <= event.x <= btn['x'] + btn['w'] and
                        btn['y'] <= event.y <= btn['y'] + btn['h']):
                        self.hovered_button = btn['id']
                        break
            if old_hover != self.hovered_button:
                self.drawing_area.queue_draw()
            return True

        if self.edit_mode:
            if self.dragging_point_idx is not None:
                self.simplified_points[self.dragging_point_idx] = (event.x, event.y)
                self.drawing_area.queue_draw()
            elif self.swipe_start_y is not None:
                delta_y = self.swipe_start_y - event.y
                self.swipe_accumulated += delta_y
                self.swipe_start_y = event.y

                if abs(self.swipe_accumulated) >= self.swipe_threshold:
                    if self.swipe_accumulated > 0:
                        self.adjust_point_count(increase=True)
                    else:
                        self.adjust_point_count(increase=False)
                    self.swipe_accumulated = 0
                    self.drawing_area.queue_draw()
            else:
                old_hover = self.hover_point_idx
                self.hover_point_idx = None
                for i, (px, py) in enumerate(self.simplified_points):
                    dist = ((event.x - px) ** 2 + (event.y - py) ** 2) ** 0.5
                    if dist < 15:
                        self.hover_point_idx = i
                        break
                if old_hover != self.hover_point_idx:
                    self.drawing_area.queue_draw()
        elif self.drawing:
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
        elif event.button == 1 and self.edit_mode:
            self.dragging_point_idx = None
            self.swipe_start_y = None
            self.swipe_accumulated = 0
        return True

    def adjust_point_count(self, increase=True):
        """Adjust the number of control points"""
        if self.original_contour_points and len(self.original_contour_points) >= 4:
            source_points = self.original_contour_points
        elif self.simplified_points and len(self.simplified_points) >= 4:
            source_points = self.simplified_points
        else:
            return

        current_count = len(self.simplified_points)

        if increase:
            new_count = min(current_count + 5, 200)
            if new_count <= current_count:
                return
        else:
            new_count = max(current_count - 5, 8)
            if new_count >= current_count:
                return

        # Resample points
        total_length = 0
        for i in range(len(source_points)):
            j = (i + 1) % len(source_points)
            dx = source_points[j][0] - source_points[i][0]
            dy = source_points[j][1] - source_points[i][1]
            total_length += (dx*dx + dy*dy) ** 0.5

        if total_length == 0:
            return

        segment_length = total_length / new_count
        new_points = [source_points[0]]
        accumulated = 0
        current_segment = 0

        for i in range(len(source_points)):
            j = (i + 1) % len(source_points)
            dx = source_points[j][0] - source_points[i][0]
            dy = source_points[j][1] - source_points[i][1]
            seg_len = (dx*dx + dy*dy) ** 0.5

            while accumulated + seg_len >= segment_length * (current_segment + 1) and current_segment < new_count - 1:
                t = (segment_length * (current_segment + 1) - accumulated) / seg_len if seg_len > 0 else 0
                x = source_points[i][0] + t * dx
                y = source_points[i][1] + t * dy
                new_points.append((x, y))
                current_segment += 1

            accumulated += seg_len

        if len(new_points) >= 4:
            self.simplified_points = new_points
            self.point_history = []

    def interpolate_points(self):
        """Create smooth curve through simplified control points"""
        if len(self.simplified_points) < 3:
            return self.simplified_points

        result = []
        points = self.simplified_points
        n = len(points)

        for i in range(n):
            p0 = points[(i - 1) % n]
            p1 = points[i]
            p2 = points[(i + 1) % n]
            p3 = points[(i + 2) % n]

            for t in [x / 10.0 for x in range(10)]:
                t2 = t * t
                t3 = t2 * t

                x = 0.5 * ((2 * p1[0]) +
                          (-p0[0] + p2[0]) * t +
                          (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                          (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)

                y = 0.5 * ((2 * p1[1]) +
                          (-p0[1] + p2[1]) * t +
                          (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                          (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)

                result.append((x, y))

        return result

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            if self.edit_mode:
                self.edit_mode = False
                self.simplified_points = []
                self.points = []
                self.dot_mode = True
                self.dot_points = []
                self.drawing_area.queue_draw()
            elif self.dot_mode and self.dot_points:
                self.dot_points = []
                self.drawing_area.queue_draw()
            else:
                self.callback(None)
                self.destroy()
                Gtk.main_quit()
        elif event.keyval == Gdk.KEY_Return:
            if not self.edit_mode and not self.drawing and len(self.points) == 0 and not self.dot_points:
                self.send_entire_image()
                return True
            elif self.dot_mode and self.dot_points and not self.edit_mode:
                if len(self.dot_points) >= 3:
                    self.points = self.dot_points.copy()
                    self.simplified_points = self.dot_points.copy()
                    self.original_contour_points = self.dot_points.copy()
                    self.dot_mode = False
                    self.edit_mode = True
                    self.point_history = []
                    self.drawing_area.queue_draw()
            elif self.edit_mode:
                self.points = self.interpolate_points()
                self.edit_mode = False
                self.process_selection()
        elif event.keyval == Gdk.KEY_BackSpace:
            if self.dot_mode and self.dot_points and not self.edit_mode:
                self.dot_points.pop()
                self.drawing_area.queue_draw()
            elif self.edit_mode and self.point_history:
                idx, old_pos = self.point_history.pop()
                if idx < len(self.simplified_points):
                    self.simplified_points[idx] = old_pos
                    self.drawing_area.queue_draw()
        elif event.keyval == Gdk.KEY_Up:
            if self.edit_mode:
                self.adjust_point_count(increase=True)
                self.drawing_area.queue_draw()
        elif event.keyval == Gdk.KEY_Down:
            if self.edit_mode:
                self.adjust_point_count(increase=False)
                self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.ctrl_held = True
            self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            self.shift_held = True
            self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_m, Gdk.KEY_M):
            if not self.edit_mode and not self.drawing and len(self.points) == 0:
                self.mode_selector_active = not self.mode_selector_active
                self.drawing_area.queue_draw()
        return True

    def on_key_release(self, widget, event):
        if self.selected_mode is None:
            if event.keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
                self.ctrl_held = False
                self.drawing_area.queue_draw()
            elif event.keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
                self.shift_held = False
                self.drawing_area.queue_draw()
        return True

    def send_entire_image(self):
        """Capture and send the entire screen"""
        if self.selection_made:
            return
        self.selection_made = True

        self._capture_mode = True
        self.drawing_area.queue_draw()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        self.hide()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        import time
        time.sleep(0.1)

        import uuid
        temp_dir = tempfile.gettempdir()
        output_path = os.path.join(temp_dir, f"circle_search_full_{uuid.uuid4().hex[:8]}.png")

        if take_screenshot_with_tool(output_path):
            self.callback(output_path)
        else:
            self.callback(None)

        self.destroy()
        Gtk.main_quit()

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

        # Save points for polygon mask before clearing
        self._capture_points = self.points.copy() if self.points else []

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

        success = take_screenshot_with_tool(cropped_path, geometry=(x, y, width, height))

        if success:
            # Apply polygon mask if we have points from dot/freehand mode
            if len(self._capture_points) >= 3:
                from PIL import ImageDraw, ImageFilter

                img = Image.open(cropped_path)
                img = img.convert('RGBA')

                # Create mask from polygon points (offset to crop origin)
                mask = Image.new('L', img.size, 0)
                draw = ImageDraw.Draw(mask)

                # Scale points to cropped image coordinates
                # For live mode, screen coords == image coords (no HiDPI scaling needed for grim crop)
                scaled_points = []
                for px, py in self._capture_points:
                    sx = int(px - x)
                    sy = int(py - y)
                    scaled_points.append((sx, sy))

                if len(scaled_points) > 2:
                    draw.polygon(scaled_points, fill=255)

                # Smooth the mask edges
                mask = mask.filter(ImageFilter.GaussianBlur(3))
                mask = mask.point(lambda x: 255 if x > 80 else 0)

                # Apply mask to alpha channel
                img.putalpha(mask)
                img.save(cropped_path, "PNG", optimize=True)

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
        self.alt_held = False  # Alt for edge-snap mode
        self.start_point = None
        self.end_point = None
        self.pixbuf = GdkPixbuf.Pixbuf.new_from_file(screenshot_path)

        # Zoom mode - magnifier that follows cursor
        self.zoom_mode = False
        self.zoom_level = 3  # 3x magnification
        self.zoom_size = 120  # Size of magnifier window
        self.mouse_x = 0
        self.mouse_y = 0

        # Edit mode - for adjusting points after tracing
        self.edit_mode = False
        self.dragging_point_idx = None
        self.hover_point_idx = None
        self.simplified_points = []  # Reduced points for editing

        # Swipe detection for adjusting point count
        self.swipe_start_y = None
        self.swipe_accumulated = 0  # Accumulated swipe distance
        self.swipe_threshold = 80  # Pixels needed before triggering adjustment
        self.original_contour_points = None  # Store original high-detail contour for resampling

        # Undo history for point movements
        self.point_history = []  # Stack of (index, old_position) tuples

        # Connect-the-dots mode (default mode - click to place points, Enter to finish)
        self.dot_mode = False  # Will be set when mode is selected
        self.dot_points = []  # Points placed in dot mode

        # Mode selector - shown at startup (M key to open)
        self.mode_selector_active = False  # Hidden by default, freehand is default
        self.selected_mode = 'freehand'  # Default to freehand for quick circle-and-go
        self.hovered_button = None  # Track which button is hovered

        # Define mode buttons (will be positioned in on_draw)
        self.mode_buttons = [
            {'id': 'dots', 'label': 'Connect Dots', 'desc': 'Click to place points'},
            {'id': 'freehand', 'label': 'Freehand', 'desc': 'Draw freely'},
            {'id': 'rectangle', 'label': 'Rectangle', 'desc': 'Drag box shape'},
        ]
        self.button_rects = []  # Will be populated in on_draw

        # Get screen dimensions
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        self.scale_factor = monitor.get_scale_factor()

        self.screen_width = geometry.width
        self.screen_height = geometry.height

        # Precompute edge map for edge-snapping (Alt + draw)
        self.edge_map = self._compute_edge_map()

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

    def _compute_edge_map(self):
        """Compute edge map for edge-snapping"""
        from PIL import ImageFilter

        img = Image.open(self.screenshot_path)

        # Scale to screen size for coordinate matching
        img = img.resize((self.screen_width, self.screen_height), Image.LANCZOS)

        # Convert to grayscale and detect edges
        gray = img.convert('L')

        # Apply Sobel-like edge detection
        edges = gray.filter(ImageFilter.FIND_EDGES)

        # Enhance edges with slight blur for smoother snapping
        edges = edges.filter(ImageFilter.GaussianBlur(1))

        return np.array(edges)

    def snap_to_edge(self, x, y, search_radius=12):
        """Light edge snapping - only snap if very close to a strong edge"""
        x = int(max(0, min(x, self.screen_width - 1)))
        y = int(max(0, min(y, self.screen_height - 1)))

        # Only snap if there's a strong edge very close (within 8 pixels)
        best_x, best_y = x, y
        best_dist = 999

        for dy in range(-search_radius, search_radius + 1, 1):
            for dx in range(-search_radius, search_radius + 1, 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.screen_width and 0 <= ny < self.screen_height:
                    strength = self.edge_map[ny, nx]
                    distance = (dx * dx + dy * dy) ** 0.5

                    # Only snap to strong edges that are very close
                    if strength > 40 and distance < best_dist and distance < 10:
                        best_dist = distance
                        best_x, best_y = nx, ny

        return best_x, best_y

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

            # Draw crosshair at center to help with placement
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            crosshair_size = 15

            # Crosshair glow
            cr.set_line_width(3)
            cr.set_source_rgba(0.55, 0.23, 0.93, 0.5)
            cr.move_to(cx - crosshair_size, cy)
            cr.line_to(cx + crosshair_size, cy)
            cr.stroke()
            cr.move_to(cx, cy - crosshair_size)
            cr.line_to(cx, cy + crosshair_size)
            cr.stroke()

            # Crosshair main line
            cr.set_line_width(1.5)
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.move_to(cx - crosshair_size, cy)
            cr.line_to(cx + crosshair_size, cy)
            cr.stroke()
            cr.move_to(cx, cy - crosshair_size)
            cr.line_to(cx, cy + crosshair_size)
            cr.stroke()

            # Center dot
            cr.set_source_rgba(0.93, 0.47, 0.86, 1.0)
            cr.arc(cx, cy, 3, 0, 2 * math.pi)
            cr.fill()

        # Connect-the-dots mode visualization
        elif self.dot_mode and self.dot_points and not self.edit_mode:
            # Draw lines connecting the dots
            if len(self.dot_points) > 1:
                def draw_dot_path():
                    cr.move_to(self.dot_points[0][0], self.dot_points[0][1])
                    for point in self.dot_points[1:]:
                        cr.line_to(point[0], point[1])
                    # Draw line back to first point to show closed shape
                    if len(self.dot_points) >= 3:
                        cr.line_to(self.dot_points[0][0], self.dot_points[0][1])

                draw_glow_stroke(draw_dot_path, line_width=3)

            # Draw each dot point with number - scale size based on point count
            num_dots = len(self.dot_points)
            # Scale from 12 (at 3 points) down to 6 (at 50+ points)
            outer_radius = max(6, 12 - (num_dots - 3) * 6 / 47)
            inner_radius = outer_radius * 0.58
            center_radius = outer_radius * 0.25
            font_size = max(8, 10 - (num_dots - 3) * 2 / 47)
            label_offset = outer_radius + 3

            for i, (px, py) in enumerate(self.dot_points):
                # Outer glow
                cr.set_source_rgba(0.55, 0.23, 0.93, 0.5)
                cr.arc(px, py, outer_radius, 0, 2 * math.pi)
                cr.fill()
                # Main dot
                cr.set_source_rgba(0.93, 0.47, 0.86, 1.0)
                cr.arc(px, py, inner_radius, 0, 2 * math.pi)
                cr.fill()
                # White center
                cr.set_source_rgba(1, 1, 1, 0.9)
                cr.arc(px, py, center_radius, 0, 2 * math.pi)
                cr.fill()
                # Point number
                cr.set_source_rgba(1, 1, 1, 0.9)
                cr.set_font_size(font_size)
                cr.move_to(px + label_offset, py - label_offset)
                cr.show_text(str(i + 1))

        elif len(self.points) > 1 and not self.edit_mode:
            # Draw freeform path with glow (hidden in edit mode)
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

        # Edit mode - show control points and interpolated curve
        if self.edit_mode and self.simplified_points:
            # Draw interpolated smooth curve (closed path)
            smooth_points = self.interpolate_points()
            if len(smooth_points) > 1:
                cr.set_source_rgba(0.2, 0.9, 0.4, 0.8)
                cr.set_line_width(3)
                cr.move_to(smooth_points[0][0], smooth_points[0][1])
                for point in smooth_points[1:]:
                    cr.line_to(point[0], point[1])
                cr.close_path()  # Connect back to start
                cr.stroke()

            # Draw control points as draggable handles
            # Scale dot size based on point count: fewer points = larger dots
            num_points = len(self.simplified_points)
            # Scale from 5 (at 8 points) down to 2 (at 200 points)
            base_size = max(2, 5 - (num_points - 8) * 3 / 192)
            drag_size = base_size * 1.6  # Proportional scaling
            hover_size = base_size * 1.4  # Proportional scaling
            inner_size = base_size * 0.5

            for i, (px, py) in enumerate(self.simplified_points):
                # Highlight hovered or dragged point
                if i == self.dragging_point_idx:
                    cr.set_source_rgba(1, 0.8, 0.2, 1)  # Yellow for dragging
                    cr.arc(px, py, drag_size, 0, 2 * math.pi)
                    cr.fill()
                elif i == self.hover_point_idx:
                    cr.set_source_rgba(0.2, 0.9, 0.9, 0.8)  # Cyan for hover
                    cr.arc(px, py, hover_size, 0, 2 * math.pi)
                    cr.fill()

                # Outer ring
                cr.set_source_rgba(0.2, 0.9, 0.4, 0.9)
                cr.arc(px, py, base_size, 0, 2 * math.pi)
                cr.fill()
                # Inner circle
                cr.set_source_rgba(1, 1, 1, 0.9)
                cr.arc(px, py, inner_size, 0, 2 * math.pi)
                cr.fill()

            # Show edit mode instructions
            cr.select_font_face("Sans", 0, 1)
            cr.set_font_size(18)
            text = f"Drag points  •  ↑↓ = ±points ({len(self.simplified_points)})  •  ENTER = confirm"
            extents = cr.text_extents(text)
            x = (self.screen_width - extents.width) / 2
            y = 50

            # Background for text
            cr.set_source_rgba(0, 0, 0, 0.7)
            cr.rectangle(x - 20, y - 25, extents.width + 40, 40)
            cr.fill()

            cr.set_source_rgba(0.4, 1, 0.6, 1)
            cr.move_to(x, y)
            cr.show_text(text)

        # Show mode selector at startup
        elif self.mode_selector_active:
            cr.select_font_face("Sans", 0, 1)  # Bold

            # Title
            text = "Select Mode"
            cr.set_font_size(32)
            extents = cr.text_extents(text)
            x = (self.screen_width - extents.width) / 2
            y = self.screen_height / 2 - 120

            # Text glow
            cr.set_source_rgba(0.55, 0.23, 0.93, 0.5)
            for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
                cr.move_to(x + dx, y + dy)
                cr.show_text(text)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.move_to(x, y)
            cr.show_text(text)

            # Draw mode buttons
            button_width = 140
            button_height = 70
            button_spacing = 20
            total_width = len(self.mode_buttons) * button_width + (len(self.mode_buttons) - 1) * button_spacing
            start_x = (self.screen_width - total_width) / 2
            button_y = self.screen_height / 2 - 30

            # Store button positions for click detection
            self.button_rects = []

            for i, btn in enumerate(self.mode_buttons):
                bx = start_x + i * (button_width + button_spacing)
                by = button_y

                self.button_rects.append({
                    'id': btn['id'],
                    'x': bx, 'y': by,
                    'w': button_width, 'h': button_height
                })

                # Button background
                is_hovered = self.hovered_button == btn['id']
                if is_hovered:
                    # Hovered - brighter
                    cr.set_source_rgba(0.55, 0.23, 0.93, 0.8)
                else:
                    cr.set_source_rgba(0.2, 0.1, 0.3, 0.7)

                # Rounded rectangle
                radius = 10
                cr.new_path()
                cr.arc(bx + radius, by + radius, radius, math.pi, 1.5 * math.pi)
                cr.arc(bx + button_width - radius, by + radius, radius, 1.5 * math.pi, 2 * math.pi)
                cr.arc(bx + button_width - radius, by + button_height - radius, radius, 0, 0.5 * math.pi)
                cr.arc(bx + radius, by + button_height - radius, radius, 0.5 * math.pi, math.pi)
                cr.close_path()
                cr.fill()

                # Button border
                if is_hovered:
                    cr.set_source_rgba(0.93, 0.47, 0.86, 1.0)
                else:
                    cr.set_source_rgba(0.55, 0.23, 0.93, 0.6)
                cr.set_line_width(2)
                cr.new_path()
                cr.arc(bx + radius, by + radius, radius, math.pi, 1.5 * math.pi)
                cr.arc(bx + button_width - radius, by + radius, radius, 1.5 * math.pi, 2 * math.pi)
                cr.arc(bx + button_width - radius, by + button_height - radius, radius, 0, 0.5 * math.pi)
                cr.arc(bx + radius, by + button_height - radius, radius, 0.5 * math.pi, math.pi)
                cr.close_path()
                cr.stroke()

                # Button label
                cr.set_font_size(14)
                label_extents = cr.text_extents(btn['label'])
                label_x = bx + (button_width - label_extents.width) / 2
                label_y = by + 28
                cr.set_source_rgba(1, 1, 1, 1.0 if is_hovered else 0.9)
                cr.move_to(label_x, label_y)
                cr.show_text(btn['label'])

                # Button description
                cr.set_font_size(11)
                desc_extents = cr.text_extents(btn['desc'])
                desc_x = bx + (button_width - desc_extents.width) / 2
                desc_y = by + 50
                cr.set_source_rgba(0.8, 0.8, 0.9, 0.8 if is_hovered else 0.6)
                cr.move_to(desc_x, desc_y)
                cr.show_text(btn['desc'])

            # ESC hint
            cr.set_font_size(14)
            hint = "ESC = cancel"
            hint_extents = cr.text_extents(hint)
            cr.set_source_rgba(0.6, 0.6, 0.7, 0.7)
            cr.move_to((self.screen_width - hint_extents.width) / 2, button_y + button_height + 40)
            cr.show_text(hint)

        # Show help text for dot mode
        elif self.dot_mode and not self.dot_points and not self.edit_mode:
            cr.select_font_face("Sans", 0, 1)  # Bold

            text = "Click to place points, ENTER to finish"
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
            text2 = "LEFT click = add point  •  RIGHT click / BACKSPACE = undo  •  ESC = cancel"
            extents2 = cr.text_extents(text2)
            x2 = (self.screen_width - extents2.width) / 2
            cr.set_source_rgba(0.8, 0.8, 0.9, 0.8)
            cr.move_to(x2, y + 45)
            cr.show_text(text2)

        # Show dot mode status when points exist
        elif self.dot_mode and self.dot_points and not self.edit_mode:
            cr.select_font_face("Sans", 0, 1)
            cr.set_font_size(18)
            text = f"{len(self.dot_points)} points  •  ENTER = finish  •  BACKSPACE = undo  •  ESC = clear"
            extents = cr.text_extents(text)
            x = (self.screen_width - extents.width) / 2
            y = 50

            # Background
            cr.set_source_rgba(0, 0, 0, 0.7)
            cr.rectangle(x - 20, y - 25, extents.width + 40, 40)
            cr.fill()

            cr.set_source_rgba(0.4, 1, 0.6, 1)
            cr.move_to(x, y)
            cr.show_text(text)

        # Show help for other modes (freehand, ellipse, rectangle, ai)
        elif self.selected_mode and not self.edit_mode and not self.drawing and len(self.points) == 0:
            cr.select_font_face("Sans", 0, 1)

            if self.selected_mode == 'freehand':
                text = "Draw around the object"
                text2 = "Hold mouse and draw  •  ENTER = full image  •  M = modes  •  ESC = cancel"
            elif self.selected_mode == 'rectangle':
                text = "Drag to draw a rectangle"
                text2 = "Click and drag  •  ESC = cancel"
            else:
                text = ""
                text2 = ""

            if text:
                cr.set_font_size(28)
                extents = cr.text_extents(text)
                x = (self.screen_width - extents.width) / 2
                y = self.screen_height / 2 - 50

                # Text glow
                cr.set_source_rgba(0.55, 0.23, 0.93, 0.5)
                for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
                    cr.move_to(x + dx, y + dy)
                    cr.show_text(text)

                cr.set_source_rgba(1, 1, 1, 0.95)
                cr.move_to(x, y)
                cr.show_text(text)

                cr.set_font_size(16)
                extents2 = cr.text_extents(text2)
                x2 = (self.screen_width - extents2.width) / 2
                cr.set_source_rgba(0.8, 0.8, 0.9, 0.8)
                cr.move_to(x2, y + 45)
                cr.show_text(text2)

        # Zoom mode - draw magnifier
        if self.zoom_mode and self.pixbuf:
            zoom_size = self.zoom_size
            zoom_level = self.zoom_level
            half_size = zoom_size // 2

            # Position magnifier offset from cursor
            mag_x = self.mouse_x + 30
            mag_y = self.mouse_y - zoom_size - 10

            # Keep magnifier on screen
            if mag_x + zoom_size > self.screen_width:
                mag_x = self.mouse_x - zoom_size - 30
            if mag_y < 0:
                mag_y = self.mouse_y + 30

            # Calculate source region from image (accounting for HiDPI)
            scale_x = self.pixbuf.get_width() / self.screen_width
            scale_y = self.pixbuf.get_height() / self.screen_height

            src_x = int((self.mouse_x - half_size / zoom_level) * scale_x)
            src_y = int((self.mouse_y - half_size / zoom_level) * scale_y)
            src_w = int((zoom_size / zoom_level) * scale_x)
            src_h = int((zoom_size / zoom_level) * scale_y)

            # Clamp to image bounds
            src_x = max(0, min(src_x, self.pixbuf.get_width() - src_w))
            src_y = max(0, min(src_y, self.pixbuf.get_height() - src_h))

            if src_w > 0 and src_h > 0:
                # Extract and scale the region
                sub_pixbuf = self.pixbuf.new_subpixbuf(src_x, src_y, src_w, src_h)
                scaled_pixbuf = sub_pixbuf.scale_simple(zoom_size, zoom_size, GdkPixbuf.InterpType.NEAREST)

                # Draw magnifier background
                cr.set_source_rgba(0.1, 0.1, 0.2, 0.95)
                cr.arc(mag_x + half_size, mag_y + half_size, half_size + 4, 0, 2 * math.pi)
                cr.fill()

                # Clip to circle and draw zoomed image
                cr.save()
                cr.arc(mag_x + half_size, mag_y + half_size, half_size, 0, 2 * math.pi)
                cr.clip()
                Gdk.cairo_set_source_pixbuf(cr, scaled_pixbuf, mag_x, mag_y)
                cr.paint()
                cr.restore()

                # Draw border
                cr.set_line_width(3)
                cr.set_source_rgba(0.55, 0.23, 0.93, 0.8)
                cr.arc(mag_x + half_size, mag_y + half_size, half_size, 0, 2 * math.pi)
                cr.stroke()

                # Draw crosshair in center
                cr.set_line_width(1)
                cr.set_source_rgba(1, 1, 1, 0.8)
                center_x = mag_x + half_size
                center_y = mag_y + half_size
                cr.move_to(center_x - 10, center_y)
                cr.line_to(center_x + 10, center_y)
                cr.move_to(center_x, center_y - 10)
                cr.line_to(center_x, center_y + 10)
                cr.stroke()

                # Show zoom level
                cr.set_font_size(10)
                cr.set_source_rgba(1, 1, 1, 0.9)
                cr.move_to(mag_x + 5, mag_y + zoom_size - 5)
                cr.show_text(f"{zoom_level}x  •  Z = toggle")

        return False

    def on_button_press(self, widget, event):
        # Mode selector: check if clicking on a button
        if self.mode_selector_active and event.button == 1:
            if hasattr(self, 'button_rects'):
                for btn in self.button_rects:
                    if (btn['x'] <= event.x <= btn['x'] + btn['w'] and
                        btn['y'] <= event.y <= btn['y'] + btn['h']):
                        self.select_mode(btn['id'])
                        return True
            return True  # Consume click even if not on button

        # Connect-the-dots mode: click to place points
        if self.dot_mode and not self.edit_mode:
            if event.button == 1:  # Left click - add point
                self.dot_points.append((event.x, event.y))
                print(f"DEBUG: Dot mode - added point {len(self.dot_points)} at ({event.x:.0f}, {event.y:.0f})")
                self.drawing_area.queue_draw()
                return True
            elif event.button == 3:  # Right click - remove last point
                if self.dot_points:
                    removed = self.dot_points.pop()
                    print(f"DEBUG: Dot mode - removed point at ({removed[0]:.0f}, {removed[1]:.0f})")
                    self.drawing_area.queue_draw()
                return True

        if event.button == 1:
            if self.edit_mode:
                # Check if clicking on a control point
                for i, (px, py) in enumerate(self.simplified_points):
                    dist = ((event.x - px) ** 2 + (event.y - py) ** 2) ** 0.5
                    if dist < 20:  # Click radius
                        self.dragging_point_idx = i
                        # Save current position for undo before dragging
                        self.point_history.append((i, (px, py)))
                        self.drawing_area.queue_draw()
                        return True
                # Not on a control point - start swipe detection
                self.swipe_start_y = event.y
                self.swipe_accumulated = 0
            else:
                self.drawing = True
                self.start_point = (event.x, event.y)
                self.end_point = (event.x, event.y)

                # Only check keyboard state if no mode was pre-selected from buttons
                if self.selected_mode is None:
                    self.ctrl_held = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
                    self.shift_held = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
                    self.alt_held = bool(event.state & Gdk.ModifierType.MOD1_MASK)
                # else: keep the ctrl_held, shift_held, alt_held from select_mode()

                # If Alt is held (freehand with edge snap), snap start point to edge
                if self.alt_held and not self.ctrl_held:
                    snapped = self.snap_to_edge(event.x, event.y)
                    self.points = [snapped]
                else:
                    self.points = [(event.x, event.y)]
        return True

    def select_mode(self, mode_id):
        """Handle mode button selection"""
        print(f"DEBUG: Selected mode: {mode_id}")
        self.mode_selector_active = False
        self.selected_mode = mode_id

        if mode_id == 'dots':
            self.dot_mode = True
            self.ctrl_held = False
            self.shift_held = False
            self.alt_held = False
        elif mode_id == 'freehand':
            self.dot_mode = False
            self.ctrl_held = False
            self.shift_held = False
            self.alt_held = False
        elif mode_id == 'rectangle':
            self.dot_mode = False
            self.ctrl_held = True
            self.shift_held = False
            self.alt_held = False

        self.drawing_area.queue_draw()

    def on_motion(self, widget, event):
        # Mode selector hover detection
        if self.mode_selector_active:
            old_hover = self.hovered_button
            self.hovered_button = None
            if hasattr(self, 'button_rects'):
                for btn in self.button_rects:
                    if (btn['x'] <= event.x <= btn['x'] + btn['w'] and
                        btn['y'] <= event.y <= btn['y'] + btn['h']):
                        self.hovered_button = btn['id']
                        break
            if old_hover != self.hovered_button:
                self.drawing_area.queue_draw()
            return True

        if self.edit_mode:
            # Handle point dragging
            if self.dragging_point_idx is not None:
                self.simplified_points[self.dragging_point_idx] = (event.x, event.y)
                self.drawing_area.queue_draw()
            elif self.swipe_start_y is not None:
                # Handle swipe for adjusting point count
                delta_y = self.swipe_start_y - event.y  # Positive = swipe up
                self.swipe_accumulated += delta_y
                self.swipe_start_y = event.y

                # Check if we've accumulated enough for an adjustment
                if abs(self.swipe_accumulated) >= self.swipe_threshold:
                    if self.swipe_accumulated > 0:
                        # Swipe up - more points
                        self.adjust_point_count(increase=True)
                    else:
                        # Swipe down - fewer points
                        self.adjust_point_count(increase=False)
                    self.swipe_accumulated = 0
                    self.drawing_area.queue_draw()
            else:
                # Update hover state
                old_hover = self.hover_point_idx
                self.hover_point_idx = None
                for i, (px, py) in enumerate(self.simplified_points):
                    dist = ((event.x - px) ** 2 + (event.y - py) ** 2) ** 0.5
                    if dist < 15:
                        self.hover_point_idx = i
                        break
                if old_hover != self.hover_point_idx:
                    self.drawing_area.queue_draw()
        elif self.drawing:
            self.end_point = (event.x, event.y)
            if not self.ctrl_held:
                self.points.append((event.x, event.y))
            self.drawing_area.queue_draw()

        # Track mouse position for zoom mode
        self.mouse_x = event.x
        self.mouse_y = event.y
        if self.zoom_mode:
            self.drawing_area.queue_draw()

        return True

    def on_button_release(self, widget, event):
        if event.button == 1 and self.drawing:
            self.drawing = False
            if self.ctrl_held and self.start_point and self.end_point:
                # Shape mode - process the selection directly
                self.process_selection()
            elif len(self.points) > 10:
                self.process_selection()
            else:
                self.points = []
                self.start_point = None
                self.end_point = None
                self.drawing_area.queue_draw()
        elif event.button == 1 and self.edit_mode:
            # Release dragged point and reset swipe
            self.dragging_point_idx = None
            self.swipe_start_y = None
            self.swipe_accumulated = 0
        return True

    def interpolate_points(self):
        """Create smooth curve through simplified control points (closed loop)"""
        if len(self.simplified_points) < 3:
            return self.simplified_points

        # Catmull-Rom spline interpolation for smooth closed curves
        result = []
        points = self.simplified_points
        n = len(points)

        for i in range(n):
            # Use modulo for closed loop - wraps around
            p0 = points[(i - 1) % n]
            p1 = points[i]
            p2 = points[(i + 1) % n]
            p3 = points[(i + 2) % n]

            # Generate points along the spline segment
            for t in [x / 10.0 for x in range(10)]:
                t2 = t * t
                t3 = t2 * t

                x = 0.5 * ((2 * p1[0]) +
                          (-p0[0] + p2[0]) * t +
                          (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                          (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)

                y = 0.5 * ((2 * p1[1]) +
                          (-p0[1] + p2[1]) * t +
                          (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                          (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)

                result.append((x, y))

        return result

    def adjust_point_count(self, increase=True):
        """Adjust the number of control points by resampling from original contour"""
        # Use original contour if available, otherwise current points
        if self.original_contour_points and len(self.original_contour_points) >= 4:
            source_points = self.original_contour_points
        elif self.simplified_points and len(self.simplified_points) >= 4:
            source_points = self.simplified_points
        else:
            return

        current_count = len(self.simplified_points)

        if increase:
            # More points - allow up to 200 (interpolation can create more than source)
            new_count = min(current_count + 5, 200)
            if new_count <= current_count:
                print(f"DEBUG: Already at max points ({current_count})")
                return
        else:
            # Fewer points - simplify
            new_count = max(current_count - 5, 8)  # Min 8 points
            if new_count >= current_count:
                print(f"DEBUG: Already at min points ({current_count})")
                return

        print(f"DEBUG: Adjusting points from {current_count} to {new_count} (source has {len(source_points)})")

        # Resample from the ORIGINAL contour to preserve shape
        # Close the path by adding first point at end for proper closed-loop calculation
        points = list(source_points)
        if len(points) > 0 and points[0] != points[-1]:
            points.append(points[0])  # Close the loop

        # Calculate total path length (now includes closing segment)
        total_length = 0
        lengths = [0]
        for i in range(1, len(points)):
            dx = points[i][0] - points[i-1][0]
            dy = points[i][1] - points[i-1][1]
            total_length += (dx*dx + dy*dy) ** 0.5
            lengths.append(total_length)

        if total_length == 0:
            return

        # Resample at uniform intervals around the closed path
        new_points = []
        for i in range(new_count):
            target_length = (i / new_count) * total_length

            # Find the segment containing this length
            for j in range(1, len(lengths)):
                if lengths[j] >= target_length:
                    # Interpolate within this segment
                    segment_start = lengths[j-1]
                    segment_end = lengths[j]
                    segment_length = segment_end - segment_start

                    if segment_length > 0:
                        t = (target_length - segment_start) / segment_length
                    else:
                        t = 0

                    x = points[j-1][0] + t * (points[j][0] - points[j-1][0])
                    y = points[j-1][1] + t * (points[j][1] - points[j-1][1])
                    new_points.append((x, y))
                    break

        if len(new_points) >= 4:
            self.simplified_points = new_points
            self.point_history = []  # Clear undo history after resampling
            print(f"DEBUG: Now have {len(self.simplified_points)} points")

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            if self.edit_mode:
                # Cancel edit mode, go back to dot mode
                self.edit_mode = False
                self.simplified_points = []
                self.points = []
                self.dot_mode = True
                self.dot_points = []
                self.drawing_area.queue_draw()
            elif self.dot_mode and self.dot_points:
                # Clear dot points
                self.dot_points = []
                self.drawing_area.queue_draw()
            else:
                self.callback(None)
                self.destroy()
                Gtk.main_quit()
        elif event.keyval == Gdk.KEY_Return:
            # Enter with no selection = send entire image
            if not self.edit_mode and not self.drawing and len(self.points) == 0 and not self.dot_points:
                print("DEBUG: Enter pressed with no selection - sending entire image")
                self.send_entire_image()
                return True
            elif self.dot_mode and self.dot_points and not self.edit_mode:
                # Finish dot mode - use the placed points as selection
                if len(self.dot_points) >= 3:
                    print(f"DEBUG: Dot mode finished with {len(self.dot_points)} points")
                    self.points = self.dot_points.copy()
                    self.simplified_points = self.dot_points.copy()
                    self.original_contour_points = self.dot_points.copy()
                    self.dot_mode = False
                    self.edit_mode = True  # Enter edit mode to refine
                    self.point_history = []  # Clear undo history
                    self.drawing_area.queue_draw()
                else:
                    print("DEBUG: Need at least 3 points to create a selection")
            elif self.edit_mode:
                # Confirm and process with interpolated smooth curve
                self.points = self.interpolate_points()
                self.edit_mode = False
                self.process_selection()
        elif event.keyval == Gdk.KEY_BackSpace:
            if self.dot_mode and self.dot_points and not self.edit_mode:
                # Remove last dot point
                removed = self.dot_points.pop()
                print(f"DEBUG: Dot mode - removed point at ({removed[0]:.0f}, {removed[1]:.0f})")
                self.drawing_area.queue_draw()
            elif self.edit_mode and self.point_history:
                # Undo last point movement
                idx, old_pos = self.point_history.pop()
                if idx < len(self.simplified_points):
                    self.simplified_points[idx] = old_pos
                    print(f"DEBUG: Undo point {idx} to {old_pos}")
                    self.drawing_area.queue_draw()
        elif event.keyval == Gdk.KEY_Up:
            if self.edit_mode:
                # Arrow up - more points
                self.adjust_point_count(increase=True)
                self.drawing_area.queue_draw()
        elif event.keyval == Gdk.KEY_Down:
            if self.edit_mode:
                # Arrow down - fewer points
                self.adjust_point_count(increase=False)
                self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self.ctrl_held = True
            self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            self.shift_held = True
            self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_m, Gdk.KEY_M):
            # M key - toggle mode selector
            if not self.edit_mode and not self.drawing and len(self.points) == 0:
                self.mode_selector_active = not self.mode_selector_active
                print(f"DEBUG: Mode selector {'opened' if self.mode_selector_active else 'closed'}")
                self.drawing_area.queue_draw()
        elif event.keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            # Z key - toggle zoom mode
            self.zoom_mode = not self.zoom_mode
            print(f"DEBUG: Zoom mode {'enabled' if self.zoom_mode else 'disabled'}")
            self.drawing_area.queue_draw()
        return True

    def on_key_release(self, widget, event):
        # Only respond to key releases if no mode was pre-selected
        # Otherwise the mode buttons lock in the ctrl/shift/alt state
        if self.selected_mode is None:
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

    def send_entire_image(self):
        """Send the entire screenshot without any selection"""
        if self.selection_made:
            return
        self.selection_made = True

        img = Image.open(self.screenshot_path)

        # Resize if too large
        max_size = 2000
        if img.width > max_size or img.height > max_size:
            ratio = min(max_size / img.width, max_size / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # Save to temp file
        temp_dir = tempfile.gettempdir()
        output_path = os.path.join(temp_dir, "circle_search_crop.png")
        img.save(output_path, "PNG", optimize=True)

        self.callback(output_path)
        self.destroy()
        Gtk.main_quit()

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

        # Apply polygon mask with transparency if we have enough points
        # This works for dot mode, freehand mode, or AI-traced mode
        if len(self.points) >= 3:
            from PIL import ImageDraw, ImageFilter

            # Convert to RGBA for transparency
            cropped = cropped.convert('RGBA')

            # Create mask from polygon points (scaled to crop coordinates)
            mask = Image.new('L', cropped.size, 0)
            draw = ImageDraw.Draw(mask)

            # Scale points to cropped image coordinates
            scaled_points = []
            for px, py in self.points:
                # Scale from screen to image, then offset to crop origin
                sx = int(px * scale_x) - x1_scaled
                sy = int(py * scale_y) - y1_scaled
                scaled_points.append((sx, sy))

            if len(scaled_points) > 2:
                draw.polygon(scaled_points, fill=255)

            # Smooth the mask edges - blur then threshold for clean edges
            mask = mask.filter(ImageFilter.GaussianBlur(3))
            mask = mask.point(lambda x: 255 if x > 80 else 0)

            # Apply mask to alpha channel
            cropped.putalpha(mask)

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
    def __init__(self, image_path, has_transparency=False):
        super().__init__(title="Circle to Search")
        self.image_path = image_path
        self.result = None
        self.has_transparency = has_transparency  # Whether image has alpha channel
        self.output_format = 'png'  # Default format
        self.feather_amount = 0  # 0 = no feather, up to 20

        # Store original image for live preview updates
        self.original_image = Image.open(image_path)
        self.preview_image = None  # Will hold the Gtk.Image widget

        self.set_default_size(520, 580)
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
        self.max_preview_size = 420
        pixbuf = self.get_preview_pixbuf()

        self.preview_image = Gtk.Image.new_from_pixbuf(pixbuf)
        self.preview_image.set_margin_top(12)
        self.preview_image.set_margin_bottom(12)
        self.preview_image.set_margin_start(12)
        self.preview_image.set_margin_end(12)

        # Center image using Box instead of deprecated Alignment
        image_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        image_box.set_halign(Gtk.Align.CENTER)
        image_box.set_valign(Gtk.Align.CENTER)
        image_box.pack_start(self.preview_image, False, False, 0)
        image_box.set_size_request(450, 280)

        frame.add(image_box)
        vbox.pack_start(frame, True, True, 0)

        # Options row (format + feather)
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        options_box.set_margin_top(8)
        vbox.pack_start(options_box, False, False, 0)

        # Format selector
        format_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        format_label = Gtk.Label(label="Format:")
        format_label.get_style_context().add_class("option-label")
        format_box.pack_start(format_label, False, False, 0)

        self.format_combo = Gtk.ComboBoxText()
        self.format_combo.append('png', 'PNG (transparent)')
        self.format_combo.append('jpg', 'JPG (smaller)')
        self.format_combo.append('webp', 'WebP (best)')
        self.format_combo.set_active_id('png')
        self.format_combo.connect('changed', self.on_format_changed)
        self.format_combo.get_style_context().add_class("format-combo")
        format_box.pack_start(self.format_combo, False, False, 0)
        options_box.pack_start(format_box, False, False, 0)

        # Feather slider
        feather_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        feather_label = Gtk.Label(label="Edge Feather:")
        feather_label.get_style_context().add_class("option-label")
        feather_box.pack_start(feather_label, False, False, 0)

        self.feather_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 20, 1)
        self.feather_scale.set_value(0)
        self.feather_scale.set_size_request(120, -1)
        self.feather_scale.set_draw_value(False)
        self.feather_scale.connect('value-changed', self.on_feather_changed)
        self.feather_scale.get_style_context().add_class("feather-scale")
        feather_box.pack_start(self.feather_scale, True, True, 0)

        self.feather_value_label = Gtk.Label(label="0px")
        self.feather_value_label.get_style_context().add_class("option-label")
        self.feather_value_label.set_size_request(35, -1)
        feather_box.pack_start(self.feather_value_label, False, False, 0)
        options_box.pack_end(feather_box, True, True, 0)

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

    def on_format_changed(self, combo):
        self.output_format = combo.get_active_id()
        # If JPG selected and image has transparency, show warning
        if self.output_format == 'jpg' and self.has_transparency:
            self.format_combo.set_tooltip_text("JPG doesn't support transparency - edges will have white background")
        else:
            self.format_combo.set_tooltip_text("")

    def on_feather_changed(self, scale):
        self.feather_amount = int(scale.get_value())
        self.feather_value_label.set_text(f"{self.feather_amount}px")
        # Update preview
        self.update_preview()

    def get_preview_pixbuf(self):
        """Generate preview pixbuf with current feather settings"""
        img = self.original_image.copy()

        # Apply feather effect if image has transparency
        if self.feather_amount > 0 and img.mode == 'RGBA':
            from PIL import ImageFilter
            alpha = img.getchannel('A')
            alpha = alpha.filter(ImageFilter.GaussianBlur(self.feather_amount))
            img.putalpha(alpha)

        # Convert PIL Image to GdkPixbuf
        if img.mode == 'RGBA':
            data = img.tobytes()
            pixbuf = GdkPixbuf.Pixbuf.new_from_data(
                data, GdkPixbuf.Colorspace.RGB, True, 8,
                img.width, img.height, img.width * 4
            )
        else:
            img_rgb = img.convert('RGB')
            data = img_rgb.tobytes()
            pixbuf = GdkPixbuf.Pixbuf.new_from_data(
                data, GdkPixbuf.Colorspace.RGB, False, 8,
                img_rgb.width, img_rgb.height, img_rgb.width * 3
            )

        # Scale to preview size
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        if width > self.max_preview_size or height > self.max_preview_size:
            scale = min(self.max_preview_size / width, self.max_preview_size / height)
            pixbuf = pixbuf.scale_simple(int(width * scale), int(height * scale), GdkPixbuf.InterpType.BILINEAR)

        return pixbuf

    def update_preview(self):
        """Update the preview image with current settings"""
        if self.preview_image:
            pixbuf = self.get_preview_pixbuf()
            self.preview_image.set_from_pixbuf(pixbuf)

    def get_output_settings(self):
        """Return current format and feather settings"""
        return {
            'format': self.output_format,
            'feather': self.feather_amount
        }

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
        label.option-label {
            color: #94a3b8;
            font-size: 12px;
        }
        .format-combo, combobox {
            background: #1e1e3f;
            color: #e2e8f0;
            border: 1px solid rgba(139, 92, 246, 0.3);
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
        }
        .feather-scale, scale {
            padding: 0;
        }
        scale trough {
            background: #1e1e3f;
            border-radius: 4px;
            min-height: 6px;
        }
        scale highlight {
            background: linear-gradient(90deg, #7c3aed 0%, #a855f7 100%);
            border-radius: 4px;
        }
        scale slider {
            background: #a855f7;
            border-radius: 50%;
            min-width: 14px;
            min-height: 14px;
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


def detect_screenshot_tool():
    """Detect available screenshot tool, preferring compositor-native ones."""
    # Try grim first (wlroots: Hyprland, Sway, etc.)
    try:
        result = subprocess.run(["grim", "-"], capture_output=True)
        if result.returncode == 0 or b"compositor" not in result.stderr:
            # grim works or failed for a reason other than protocol support
            if result.returncode == 0:
                return "grim"
    except FileNotFoundError:
        pass  # grim not installed, try other tools

    # Try spectacle (KDE Plasma)
    if subprocess.run(["which", "spectacle"], capture_output=True).returncode == 0:
        return "spectacle"

    # Try GNOME Shell D-Bus Screenshot API (GNOME 42+)
    # gnome-screenshot is deprecated and broken on GNOME 49+
    try:
        result = subprocess.run(
            ["gdbus", "introspect", "--session",
             "--dest", "org.gnome.Shell.Screenshot",
             "--object-path", "/org/gnome/Shell/Screenshot"],
            capture_output=True, timeout=2
        )
        if result.returncode == 0:
            return "gnome-shell"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback to grim anyway (let it fail with proper error)
    return "grim"


def take_screenshot_with_tool(output_path, tool=None, geometry=None):
    """
    Take a screenshot using the specified or auto-detected tool.

    Args:
        output_path: Path to save the screenshot
        tool: Screenshot tool to use (auto-detect if None)
        geometry: Optional tuple (x, y, width, height) for region capture

    Returns:
        True if successful, False otherwise
    """
    if tool is None:
        tool = detect_screenshot_tool()

    if tool == "grim":
        if geometry:
            x, y, w, h = geometry
            geom_str = f"{int(x)},{int(y)} {int(w)}x{int(h)}"
            result = subprocess.run(["grim", "-g", geom_str, output_path], capture_output=True)
        else:
            result = subprocess.run(["grim", output_path], capture_output=True)
        return result.returncode == 0

    elif tool == "spectacle":
        # spectacle: -b = background, -n = no notification, -o = output file
        result = subprocess.run(
            ["spectacle", "-b", "-n", "-o", output_path],
            capture_output=True
        )
        if result.returncode != 0:
            return False

        # If geometry requested, crop the full screenshot
        if geometry:
            try:
                x, y, w, h = geometry
                img = Image.open(output_path)
                cropped = img.crop((int(x), int(y), int(x + w), int(y + h)))
                cropped.save(output_path)
            except Exception:
                return False
        return True

    elif tool == "gnome-shell":
        # Use GNOME Shell D-Bus Screenshot API (works on GNOME 42+)
        # gnome-screenshot is deprecated and broken on GNOME 49+
        result = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.gnome.Shell.Screenshot",
             "--object-path", "/org/gnome/Shell/Screenshot",
             "--method", "org.gnome.Shell.Screenshot.Screenshot",
             "false", "false", output_path],
            capture_output=True
        )
        if result.returncode != 0:
            return False

        # If geometry requested, crop the full screenshot
        if geometry:
            try:
                x, y, w, h = geometry
                img = Image.open(output_path)
                cropped = img.crop((int(x), int(y), int(x + w), int(y + h)))
                cropped.save(output_path)
            except Exception:
                return False
        return True

    return False


def take_screenshot():
    temp_dir = tempfile.gettempdir()
    screenshot_path = os.path.join(temp_dir, "circle_search_screenshot.png")
    if take_screenshot_with_tool(screenshot_path):
        return screenshot_path
    return None


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
            subprocess.run(["notify-send", "Error", "Failed to take screenshot. Install grim (wlroots) or spectacle (KDE). GNOME uses built-in D-Bus API."])
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

    # Check if image has transparency
    img = Image.open(crop_path[0])
    has_transparency = img.mode == 'RGBA' and img.getchannel('A').getextrema()[0] < 255

    # Copy image to clipboard
    copy_to_clipboard_image(crop_path[0])

    # Save to persistent location (will be updated with format choice)
    persistent_path = "/tmp/circle_search_upload.png"
    subprocess.run(["cp", crop_path[0], persistent_path], capture_output=True)

    # Phase 2: Show preview dialog
    dialog = ImagePreviewDialog(crop_path[0], has_transparency=has_transparency)
    dialog.show_all()
    Gtk.main()

    choice = dialog.result
    output_settings = dialog.get_output_settings()
    dialog.destroy()

    # Apply output settings (format and feather) if needed
    if output_settings['feather'] > 0 or output_settings['format'] != 'png':
        img = Image.open(crop_path[0])

        # Apply feather effect to edges
        if output_settings['feather'] > 0 and img.mode == 'RGBA':
            from PIL import ImageFilter
            # Get alpha channel and apply gaussian blur for feathering
            alpha = img.getchannel('A')
            feather_amount = output_settings['feather']
            alpha = alpha.filter(ImageFilter.GaussianBlur(feather_amount))
            img.putalpha(alpha)

        # Convert format
        fmt = output_settings['format']
        if fmt == 'jpg':
            # JPG doesn't support transparency - composite on white
            if img.mode == 'RGBA':
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            else:
                img = img.convert('RGB')
            persistent_path = "/tmp/circle_search_upload.jpg"
            img.save(persistent_path, "JPEG", quality=90)
        elif fmt == 'webp':
            persistent_path = "/tmp/circle_search_upload.webp"
            img.save(persistent_path, "WEBP", quality=90)
        else:  # png
            persistent_path = "/tmp/circle_search_upload.png"
            img.save(persistent_path, "PNG", optimize=True)

        # Update clipboard with new version
        copy_to_clipboard_image(persistent_path)

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
