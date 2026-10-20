"""Building blocks of the conversation window: labels, the primary button, cards, page thumbnails and the image and
text viewers. Look comes from ``theme`` (object names and tokens); behaviour lives here."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import theme


def label(text: str = "", name: str = "", wrap: bool = True, selectable: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def primary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("Primary")
    b.setDefault(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class Card(QFrame):
    """A white rounded panel with an optional title and helper line."""

    def __init__(self, title: str = "", helper: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 16, 20, 18)
        self.body.setSpacing(8)
        if title:
            self.title = label(title, "CardTitle")
            self.body.addWidget(self.title)
        if helper:
            self.helper = label(helper, "Muted")
            self.body.addWidget(self.helper)

    def add(self, w) -> None:
        if isinstance(w, QLayout):
            self.body.addLayout(w)
        else:
            self.body.addWidget(w)


class Thumb(QWidget):
    """A clickable preview image, scaled down to the width it is given (never up)."""
    clicked = Signal(str)

    MAX_WIDTH = 760

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.pm = QPixmap(str(self.path))
        if not self.pm.isNull() and self.pm.width() > self.MAX_WIDTH:
            self.pm = self.pm.scaledToWidth(self.MAX_WIDTH, Qt.TransformationMode.SmoothTransformation)
        self._scaled: Optional[QPixmap] = None
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{self.path.name} — click to zoom")

    def hasHeightForWidth(self) -> bool:
        return not self.pm.isNull()

    def heightForWidth(self, w: int) -> int:
        if self.pm.isNull() or self.pm.width() == 0:
            return 0
        return int(min(w, self.pm.width()) * self.pm.height() / self.pm.width()) + 2

    def sizeHint(self) -> QSize:
        w = self.pm.width() if not self.pm.isNull() else 0
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:
        return QSize(160, self.heightForWidth(160))

    def paintEvent(self, event):
        if self.pm.isNull():
            return
        target = min(self.width() - 2, self.pm.width())
        if self._scaled is None or self._scaled.width() != target:
            self._scaled = self.pm.scaledToWidth(max(1, target), Qt.TransformationMode.SmoothTransformation)
        p = QPainter(self)
        p.drawPixmap(1, 1, self._scaled)
        t = theme.current
        if t is not None:
            p.setPen(QColor(t.border))
            p.drawRect(0, 0, self._scaled.width() + 1, self._scaled.height() + 1)
        p.end()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(str(self.path))
        super().mouseReleaseEvent(event)


class ImageViewer(QDialog):
    """Before/after page images at full size: Fit, 100 %, Ctrl+wheel zoom."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.resize(1200, 820)
        self.original = QPixmap(str(path))
        self.zoom = 1.0
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        fit, actual = QPushButton("Fit"), QPushButton("100 %")
        fit.clicked.connect(self.fit)
        actual.clicked.connect(lambda: self.set_zoom(1.0))
        bar.addWidget(fit)
        bar.addWidget(actual)
        bar.addStretch(1)
        bar.addWidget(label("Ctrl + mouse wheel to zoom", "Muted", wrap=False))
        lay.addLayout(bar)
        self.area = QScrollArea()
        self.area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image = QLabel()
        self.area.setWidget(self.image)
        lay.addWidget(self.area, 1)
        QTimer.singleShot(0, self.fit)

    def set_zoom(self, z: float) -> None:
        self.zoom = max(0.1, min(4.0, z))
        if self.original.isNull():
            return
        pm = self.original.scaled(self.original.size() * self.zoom, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
        self.image.setPixmap(pm)
        self.image.resize(pm.size())

    @Slot()
    def fit(self):
        if self.original.isNull():
            return
        avail = self.area.viewport().size()
        self.set_zoom(min(avail.width() / self.original.width(), avail.height() / self.original.height(), 1.0))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15))
            event.accept()
        else:
            super().wheelEvent(event)


class TextDialog(QDialog):
    """Shows plain text (the license) inside the app, in a monospaced box that can be selected and copied."""

    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setFont(theme.mono_font(10))
        self.browser.setPlainText(text)
        lay.addWidget(self.browser, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
