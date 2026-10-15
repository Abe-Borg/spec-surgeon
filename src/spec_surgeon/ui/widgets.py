"""Building blocks of the window: cards, path pickers (drag-and-drop), the drop zone, chips, stat tiles, info bars,
empty states, status badges, the step rail's delegate, link labels, image and Markdown viewers. Look comes from
``theme`` (object names and tokens); behaviour lives here."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import theme


def repolish(w: QWidget) -> None:
    """Re-apply the style sheet after a dynamic property (state/tone) changed."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def label(text: str = "", name: str = "", wrap: bool = True, selectable: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def eyebrow(text: str) -> QLabel:
    """The small capitals line above a page title ("STEP 2 OF 4"); colour from the theme, letters from here."""
    lab = QLabel(text.upper())
    lab.setObjectName("Eyebrow")
    f = QFont(QApplication.font())
    f.setPointSizeF(8.5)
    f.setBold(True)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
    lab.setFont(f)
    return lab


def pill(text: str = "", tone: str = "") -> QLabel:
    """A small rounded status label (key status, counts)."""
    lab = QLabel(text)
    lab.setObjectName("Pill")
    lab.setProperty("tone", tone)
    return lab


def set_pill(lab: QLabel, text: str, tone: str = "") -> None:
    lab.setText(text)
    lab.setProperty("tone", tone)
    repolish(lab)


def link_label(text: str, url: str) -> QLabel:
    """A clickable link that opens in the browser (or the mail program for ``mailto:``)."""
    lab = QLabel(f'<a href="{url}">{text}</a>')
    lab.setOpenExternalLinks(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    lab.setToolTip(url)
    lab.setCursor(Qt.CursorShape.PointingHandCursor)
    return lab


def primary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("Primary")
    b.setDefault(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def link_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("LinkButton")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFlat(True)
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


class InfoBar(QFrame):
    """A coloured message strip (info / ok / warn / error) with an optional action and a close button."""
    closed = Signal()

    def __init__(self, parent=None, closable: bool = True):
        super().__init__(parent)
        self.setObjectName("InfoBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 8, 9)
        lay.setSpacing(10)
        self.title = label("", "InfoTitle", wrap=False)
        self.text = label("", selectable=True)
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.title, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        lay.addLayout(self.actions)
        self._action_buttons: List[QPushButton] = []
        if closable:
            x = QPushButton("✕")
            x.setObjectName("LinkButton")
            x.setToolTip("Dismiss")
            x.setFixedWidth(24)
            x.clicked.connect(self._close)
            lay.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    def show_message(self, tone: str, title: str, text: str,
                     actions: Optional[List[tuple]] = None) -> None:
        self.setProperty("tone", tone)
        repolish(self)
        self.title.setText(title)
        self.title.setVisible(bool(title))
        self.text.setText(text)
        for b in self._action_buttons:
            b.deleteLater()
        self._action_buttons = []
        for text_, fn in actions or []:
            b = QPushButton(text_)
            b.clicked.connect(fn)
            self.actions.addWidget(b)
            self._action_buttons.append(b)
        self.show()

    @Slot()
    def _close(self):
        self.hide()
        self.closed.emit()


class EmptyState(QFrame):
    """What a step shows before it has anything: a title, a sentence, and the button that leads to the right step."""

    def __init__(self, title: str, text: str, button: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("EmptyState")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 28, 48, 28)
        lay.setSpacing(6)
        # no layout-level alignment: a word-wrapped label needs the full width to report its height correctly
        self.title = label(title, "EmptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = label(text, "Muted")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.title)
        lay.addWidget(self.text)
        self.button: Optional[QPushButton] = None
        if button:
            self.button = QPushButton(button)
            self.button.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addSpacing(8)
            lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)


def _dropped_local_path(event, kind: str, suffix: str) -> Optional[Path]:
    """The first dropped local path that fits: a folder (a dropped file picks its folder) or a file with ``suffix``."""
    md = event.mimeData()
    if not md.hasUrls():
        return None
    for url in md.urls():
        if url.isLocalFile():
            p = Path(url.toLocalFile())
            if kind == "dir" and p.is_file():
                p = p.parent
            if (kind == "dir" and p.is_dir()) or (kind == "file" and p.is_file() and p.suffix.lower() == suffix):
                return p
    return None


class PathPicker(QWidget):
    """A path field with Browse… and drag-and-drop. ``kind``: "dir" or "file" (``suffix`` filters files)."""
    changed = Signal(str)

    def __init__(self, kind: str, placeholder: str = "", suffix: str = ".docx",
                 browse: Optional[Callable[[str], str]] = None, parent=None):
        super().__init__(parent)
        self.kind, self.suffix, self._browse = kind, suffix.lower(), browse
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self._text_changed)
        self.button = QPushButton("Browse…")
        self.button.clicked.connect(self._on_browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)
        self.setAcceptDrops(True)
        self.edit.setAcceptDrops(False)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value or "")

    def set_invalid(self, invalid: bool) -> None:
        self.edit.setProperty("invalid", "true" if invalid else "false")
        repolish(self.edit)

    @Slot(str)
    def _text_changed(self, value: str):
        self.changed.emit(value.strip())

    @Slot()
    def _on_browse(self):
        if self._browse:
            chosen = self._browse(self.text())
            if chosen:
                self.setText(chosen)

    def _dropped_path(self, event) -> Optional[Path]:
        return _dropped_local_path(event, self.kind, self.suffix)

    def dragEnterEvent(self, event):
        if self._dropped_path(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event):
        p = self._dropped_path(event)
        if p is not None:
            self.setText(str(p))
            event.acceptProposedAction()


class DropZone(QFrame):
    """A big dashed target for the specs folder: drop the folder (or any .docx in it) here, or press Browse….
    Shown while no folder is chosen; ``dropped`` carries the folder's path."""
    dropped = Signal(str)
    browse = Signal()

    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 22, 48, 22)
        lay.setSpacing(4)
        self.title = label(title, "DropTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = label(text, "Muted")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.button = QPushButton("Browse for the folder…")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.browse)
        lay.addWidget(self.title)
        lay.addWidget(self.text)
        lay.addSpacing(8)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)

    def _set_active(self, on: bool) -> None:
        self.setProperty("active", "true" if on else "false")
        repolish(self)

    def dragEnterEvent(self, event):
        if _dropped_local_path(event, "dir", ".docx") is not None:
            self._set_active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._set_active(False)

    def dropEvent(self, event):
        self._set_active(False)
        p = _dropped_local_path(event, "dir", ".docx")
        if p is not None:
            self.dropped.emit(str(p))
            event.acceptProposedAction()


class FlowLayout(QLayout):
    """Lays widgets out left to right, wrapping (Qt's flow-layout example)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._spacing, 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class ChipRow(QWidget):
    """Clickable suggestion chips, wrapping."""
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self)
        self._buttons: List[QPushButton] = []

    def set_items(self, items: List[str]) -> None:
        for b in self._buttons:
            self.flow.removeWidget(b)
            b.deleteLater()
        self._buttons = []
        for text in items:
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(self._on_click)
            self.flow.addWidget(b)
            self._buttons.append(b)

    @Slot()
    def _on_click(self):
        self.picked.emit(self.sender().text())


class StatTile(QFrame):
    """A number with a caption; the tone colours the number and the tile's top edge."""

    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        self.setObjectName("StatTile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(0)
        self.value = label("–", "StatValue", wrap=False)
        self.caption = label(caption, "StatLabel", wrap=False)
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def set(self, value, tone: str = "") -> None:
        self.value.setText(str(value))
        self.value.setProperty("tone", tone)
        self.setProperty("tone", tone)
        repolish(self.value)
        repolish(self)


BADGE_ROLE = Qt.ItemDataRole.UserRole + 1
STEP_STATE_ROLE = Qt.ItemDataRole.UserRole + 11      # "done" | "current" | "todo"
STEP_STATUS_ROLE = Qt.ItemDataRole.UserRole + 12     # the line under the step's title


class BadgeDelegate(QStyledItemDelegate):
    """Draws the status column as a coloured pill (PASS / FAIL / Exception / Planned)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        kind = index.data(BADGE_ROLE)
        if not kind:
            return super().paint(painter, option, index)
        t = theme.current or theme.tokens(False, theme.FALLBACK_ACCENT)
        fg, bg = {"pass": (t.ok_fg, t.ok_bg), "fail": (t.err_fg, t.err_bg), "exception": (t.warn_fg, t.warn_bg),
                  "planned": (t.info_fg, t.info_bg)}.get(kind, (t.text, t.surface_alt))
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)   # background only
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(opt.font)
        font.setBold(True)
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1))
        painter.setFont(font)
        fm = painter.fontMetrics()
        w = fm.horizontalAdvance(text) + 18
        h = fm.height() + 6
        r = QRect(opt.rect.x() + 6, opt.rect.center().y() - h // 2, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(r, h / 2, h / 2)
        painter.setPen(QColor(fg))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width() + 24, max(s.height(), 30))


class StepDelegate(QStyledItemDelegate):
    """Paints one step of the sidebar: a numbered circle on a vertical rail (green check when done, accent when
    current, outlined when still to do), the step's title and a one-line status under it."""

    ROW_HEIGHT = 60
    CIRCLE = 24
    LEFT = 16
    TEXT_LEFT = 54

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        t = theme.current or theme.tokens(False, theme.FALLBACK_ACCENT)
        r = option.rect
        state = index.data(STEP_STATE_ROLE) or "todo"
        status = index.data(STEP_STATUS_ROLE) or ""
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        number = index.row() + 1
        last = index.row() == index.model().rowCount() - 1
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if state == "current" or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.sidebar_selected if state == "current" else t.sidebar_hover))
            painter.drawRoundedRect(r.adjusted(6, 2, -6, -2), 8, 8)
        cx = r.x() + self.LEFT + self.CIRCLE / 2
        cy = r.center().y() + 0.5
        half = self.CIRCLE / 2
        rail = QPen(QColor(t.rail), 2)
        painter.setPen(rail)
        if index.row() > 0:
            painter.drawLine(QPoint(int(cx), r.top()), QPoint(int(cx), int(cy - half - 4)))
        if not last:
            painter.drawLine(QPoint(int(cx), int(cy + half + 4)), QPoint(int(cx), r.bottom() + 1))
        circle = QRectF(cx - half, cy - half, self.CIRCLE, self.CIRCLE)
        num_font = QFont(option.font)
        num_font.setBold(True)
        num_font.setPointSizeF(8.5)
        painter.setFont(num_font)
        if state == "done":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.ok_fg))
            painter.drawEllipse(circle)
            check = QPen(QColor("#FFFFFF"), 2.2)
            check.setCapStyle(Qt.PenCapStyle.RoundCap)
            check.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check)
            painter.drawPolyline([QPoint(int(cx - 5), int(cy)), QPoint(int(cx - 1.5), int(cy + 3.5)),
                                  QPoint(int(cx + 5.5), int(cy - 4))])
        elif state == "current":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawEllipse(circle)
            painter.setPen(QColor(t.accent_text))
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(number))
        else:
            painter.setPen(QPen(QColor(t.sidebar_muted), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle)
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(number))
        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold if state == "current" else QFont.Weight.Medium)
        painter.setFont(title_font)
        painter.setPen(QColor(t.sidebar_text if state != "todo" else t.sidebar_muted))
        text_x = r.x() + self.TEXT_LEFT
        text_w = r.right() - text_x - 10
        fm = painter.fontMetrics()
        title_rect = QRect(text_x, r.y() + 10, text_w, fm.height())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(title, Qt.TextElideMode.ElideRight, text_w))
        status_font = QFont(option.font)
        status_font.setPointSizeF(max(7.5, option.font.pointSizeF() - 1))
        painter.setFont(status_font)
        painter.setPen(QColor(t.sidebar_muted))
        fm = painter.fontMetrics()
        status_rect = QRect(text_x, title_rect.bottom() + 3, text_w, fm.height())
        painter.drawText(status_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(status, Qt.TextElideMode.ElideRight, text_w))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(max(option.rect.width(), 160), self.ROW_HEIGHT)


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


class MarkdownDialog(QDialog):
    """Shows a Markdown file or text (the batch report, release notes) inside the app."""

    def __init__(self, title: str, markdown: str, parent=None, base: Optional[Path] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 760)
        lay = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        if base is not None:
            self.browser.setSearchPaths([str(base)])
        self.browser.setMarkdown(markdown)
        lay.addWidget(self.browser, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)


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
