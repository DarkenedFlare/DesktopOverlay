from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


MIN_SCALE = 10
MAX_SCALE = 400
RESIZE_HANDLE_SIZE = 24
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
APP_NAME = "DesktopOverlay"


def resource_path(name: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / name


APP_ICON_PATH = resource_path("DesktopOverlay.png")


class OverlayWindow(QWidget):
    scale_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.original_pixmap: QPixmap | None = None
        self.display_pixmap: QPixmap | None = None
        self.scale_percent = 100
        self.opacity_percent = 100
        self.always_on_top = True
        self.locked = False
        self.drag_mode = "move"
        self.drag_origin = QPoint()
        self.start_geometry = QRect()

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self._apply_window_flags()

    def has_image(self) -> bool:
        return self.original_pixmap is not None and not self.original_pixmap.isNull()

    def set_image(self, pixmap: QPixmap) -> None:
        self.original_pixmap = pixmap
        self._refresh_pixmap(center=True)
        self.show()
        self.raise_()

    def set_scale_percent(self, value: int, *, center: bool = False) -> None:
        bounded = max(MIN_SCALE, min(MAX_SCALE, int(value)))
        if bounded == self.scale_percent and self.display_pixmap is not None:
            return
        self.scale_percent = bounded
        self._refresh_pixmap(center=center)

    def set_opacity_percent(self, value: int) -> None:
        self.opacity_percent = max(5, min(100, int(value)))
        self.setWindowOpacity(self.opacity_percent / 100)

    def set_locked(self, locked: bool) -> None:
        if self.locked == locked:
            return
        self.locked = locked
        self._apply_window_flags()

    def set_always_on_top(self, always_on_top: bool) -> None:
        if self.always_on_top == always_on_top:
            return
        self.always_on_top = always_on_top
        self._apply_window_flags()

    def center_on_screen(self) -> None:
        if not self.has_image():
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.x() + (available.width() - self.width()) // 2
        y = available.y() + (available.height() - self.height()) // 2
        self.move(x, y)

    def _apply_window_flags(self) -> None:
        geometry = self.geometry()
        was_visible = self.isVisible()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool

        if self.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint

        self.hide()
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.locked)
        self.setWindowOpacity(self.opacity_percent / 100)
        self.setFocusPolicy(
            Qt.FocusPolicy.NoFocus if self.locked else Qt.FocusPolicy.StrongFocus
        )

        if geometry.isValid():
            self.setGeometry(geometry)
        if was_visible:
            self.show()
            self.raise_()
            if not self.locked:
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._apply_native_input_state()

    def _apply_native_input_state(self) -> None:
        hwnd = int(self.winId())
        exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        exstyle |= WS_EX_LAYERED
        if self.locked:
            exstyle |= WS_EX_TRANSPARENT
        else:
            exstyle &= ~WS_EX_TRANSPARENT

        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle)
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )

    def _refresh_pixmap(self, *, center: bool = False) -> None:
        if not self.has_image():
            return

        target_width = max(1, round(self.original_pixmap.width() * self.scale_percent / 100))
        target_height = max(1, round(self.original_pixmap.height() * self.scale_percent / 100))
        self.display_pixmap = self.original_pixmap.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        previous_position = self.pos()
        self.resize(self.display_pixmap.size())
        if center or not self.isVisible():
            self.center_on_screen()
        else:
            self.move(previous_position)

        self.scale_changed.emit(self.scale_percent)
        self.update()

    def paintEvent(self, _event) -> None:
        if self.display_pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, self.display_pixmap)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.locked or not self.has_image() or event.button() != Qt.MouseButton.LeftButton:
            return

        self.drag_origin = event.globalPosition().toPoint()
        self.start_geometry = self.geometry()
        if self._is_resize_zone(event.position().toPoint()):
            self.drag_mode = "resize"
        else:
            self.drag_mode = "move"
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.locked or not self.has_image():
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.drag_origin
            if self.drag_mode == "resize":
                target_width = max(1, self.start_geometry.width() + delta.x())
                new_scale = round(target_width / self.original_pixmap.width() * 100)
                new_scale = max(MIN_SCALE, min(MAX_SCALE, new_scale))
                if new_scale != self.scale_percent:
                    top_left = self.start_geometry.topLeft()
                    self.set_scale_percent(new_scale)
                    self.move(top_left)
            else:
                self.move(self.start_geometry.topLeft() + delta)
            return

        if self._is_resize_zone(event.position().toPoint()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self.drag_mode = "move"

    def _is_resize_zone(self, point: QPoint) -> bool:
        return (
            point.x() >= self.width() - RESIZE_HANDLE_SIZE
            and point.y() >= self.height() - RESIZE_HANDLE_SIZE
        )


class ControlPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setFixedWidth(420)

        self.overlay = OverlayWindow()
        self.image_path: Path | None = None

        if APP_ICON_PATH.exists():
            icon = QIcon(str(APP_ICON_PATH))
            self.setWindowIcon(icon)
            self.overlay.setWindowIcon(icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title_label = QLabel(APP_NAME)
        title_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title_label)

        subtitle_label = QLabel(
            "Load an image, move it on screen, and lock it when you want clicks to pass through."
        )
        subtitle_label.setWordWrap(True)
        subtitle_label.setStyleSheet("color: #555555;")
        layout.addWidget(subtitle_label)

        state_row = QHBoxLayout()
        state_row.addWidget(QLabel("State"))
        self.state_badge = QLabel("Unlocked")
        self.state_badge.setStyleSheet("font-weight: 700;")
        state_row.addWidget(self.state_badge)
        state_row.addStretch(1)
        layout.addLayout(state_row)

        self.preview_label = QLabel("No image loaded")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(160)
        self.preview_label.setStyleSheet(
            "border: 1px solid #c8c8c8; background: #f7f7f7; color: #666666;"
        )
        layout.addWidget(self.preview_label)

        self.image_name_label = QLabel("No file selected")
        self.image_name_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.image_name_label)

        self.image_meta_label = QLabel("Open an image to start the overlay.")
        self.image_meta_label.setStyleSheet("color: #555555;")
        layout.addWidget(self.image_meta_label)

        open_row = QHBoxLayout()
        open_row.addWidget(QLabel("Image"))
        open_button = QPushButton("Open Image")
        open_button.clicked.connect(self.open_image)
        open_row.addWidget(open_button, 1)
        layout.addLayout(open_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size"))
        self.scale_label = QLabel("Drag overlay corner")
        size_row.addWidget(self.scale_label, 1)
        layout.addLayout(size_row)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.valueChanged.connect(self.on_opacity_changed)
        opacity_row.addWidget(self.opacity_slider, 1)
        self.opacity_label = QLabel("100%")
        opacity_row.addWidget(self.opacity_label)
        layout.addLayout(opacity_row)

        self.lock_button = QPushButton("Lock Overlay")
        self.lock_button.setCheckable(True)
        self.lock_button.toggled.connect(self.on_lock_toggled)
        layout.addWidget(self.lock_button)

        help_text = QLabel(
            "Unlocked: drag the image to move it and drag the bottom-right corner to resize it. Locked: the overlay stays visible but mouse clicks pass through it."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: #555555;")
        layout.addWidget(help_text)

        action_row = QHBoxLayout()
        center_button = QPushButton("Center Overlay")
        center_button.clicked.connect(self.center_overlay)
        action_row.addWidget(center_button)

        hide_button = QPushButton("Hide Overlay")
        hide_button.clicked.connect(self.hide_overlay)
        action_row.addWidget(hide_button)

        show_button = QPushButton("Show Overlay")
        show_button.clicked.connect(self.show_overlay)
        action_row.addWidget(show_button)
        layout.addLayout(action_row)

        self.status_label = QLabel("Load an image to begin.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #555555;")
        layout.addWidget(self.status_label)

        self.overlay.scale_changed.connect(self.update_scale_label)
        self.overlay.set_opacity_percent(self.opacity_slider.value())
        self._update_lock_ui(False)

    def _update_preview(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("No image loaded")
            return

        preview = pixmap.scaled(
            396,
            180,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(preview)

    def _update_lock_ui(self, locked: bool) -> None:
        self.lock_button.blockSignals(True)
        self.lock_button.setChecked(locked)
        self.lock_button.setText("Unlock Overlay" if locked else "Lock Overlay")
        self.lock_button.blockSignals(False)

        if locked:
            self.state_badge.setText("Locked")
            self.state_badge.setStyleSheet("font-weight: 700; color: #8a5a00;")
        else:
            self.state_badge.setText("Unlocked")
            self.state_badge.setStyleSheet("font-weight: 700; color: #20623b;")

    def open_image(self) -> None:
        file_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open image",
            "",
            "Image files (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All files (*.*)",
        )
        if not file_path:
            return

        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            QMessageBox.critical(self, "Open image", "Could not open the selected image file.")
            return

        self.image_path = Path(file_path)
        self.overlay.set_image(pixmap)
        self.overlay.set_locked(self.lock_button.isChecked())
        self.overlay.set_opacity_percent(self.opacity_slider.value())
        self._update_preview(pixmap)
        self.image_name_label.setText(self.image_path.name)
        self.image_meta_label.setText(f"{pixmap.width()} x {pixmap.height()} source image")
        self.status_label.setText(f"Loaded: {self.image_path.name}")

    def on_opacity_changed(self, value: int) -> None:
        self.opacity_label.setText(f"{value}%")
        self.overlay.set_opacity_percent(value)

    def on_lock_toggled(self, checked: bool) -> None:
        self.overlay.set_locked(checked)
        self._update_lock_ui(checked)
        if checked:
            self.status_label.setText("Overlay locked. It stays on top and mouse clicks pass through it.")
        else:
            if self.overlay.has_image():
                self.overlay.show()
                self.overlay.raise_()
                self.overlay.activateWindow()
            self.status_label.setText("Overlay unlocked. Drag to move it, or drag the bottom-right corner to resize.")

    def center_overlay(self) -> None:
        if not self.overlay.has_image():
            self.status_label.setText("Load an image before centering the overlay.")
            return
        self.overlay.center_on_screen()
        self.overlay.show()
        self.overlay.raise_()

    def hide_overlay(self) -> None:
        self.overlay.hide()
        self.status_label.setText("Overlay hidden.")

    def show_overlay(self) -> None:
        if not self.overlay.has_image():
            self.status_label.setText("Load an image before showing the overlay.")
            return
        self.overlay.show()
        self.overlay.raise_()
        self.status_label.setText("Overlay shown.")

    def update_scale_label(self, value: int) -> None:
        if not self.overlay.has_image() or self.overlay.display_pixmap is None:
            self.scale_label.setText("Drag overlay corner")
            return
        self.scale_label.setText(
            f"{self.overlay.display_pixmap.width()} x {self.overlay.display_pixmap.height()} ({value}%)"
        )


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    panel = ControlPanel()
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()