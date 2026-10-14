"""The window's look: a Windows 11-like light/dark theme on Qt's Fusion style, the same on every Windows version
(and in the screenshots and tests, which run on Linux).

- Colours follow Windows' light/dark setting (or Settings → Appearance), and the accent is the Windows accent
  colour when Qt reports one.
- Settings → Appearance → "Windows native controls" switches to Qt's own ``windows11`` style (Windows 11 only)
  with only the layout rules below (cards, titles, badges); ``SPEC_SURGEON_QT_STYLE`` forces any Qt style.
- Fonts: Segoe UI Variable / Segoe UI on Windows; Cascadia Mono / Consolas for the activity log.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

STYLE_ENV = "SPEC_SURGEON_QT_STYLE"
FALLBACK_ACCENT = "#0F6CBD"
UI_FONTS = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans")
MONO_FONTS = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono")


@dataclass(frozen=True)
class Tokens:
    dark: bool
    accent: str
    accent_text: str
    window: str
    surface: str          # cards
    surface_alt: str      # table alternate rows, nav hover
    border: str
    input_bg: str
    input_border: str
    text: str
    muted: str
    nav_selected: str
    ok_fg: str
    ok_bg: str
    err_fg: str
    err_bg: str
    warn_fg: str
    warn_bg: str
    info_fg: str
    info_bg: str


def tokens(dark: bool, accent: str) -> Tokens:
    if dark:
        return Tokens(True, accent, "#000000" if QColor(accent).lightness() > 150 else "#FFFFFF",
                      window="#202020", surface="#2B2B2B", surface_alt="#323232", border="#3A3A3A",
                      input_bg="#2D2D2D", input_border="#454545", text="#FFFFFF", muted="#C5C5C5",
                      nav_selected="#2D2D2D", ok_fg="#6CCB5F", ok_bg="#393D1B", err_fg="#FF99A4", err_bg="#442726",
                      warn_fg="#FCE100", warn_bg="#433519", info_fg="#99EBFF", info_bg="#1F3347")
    return Tokens(False, accent, "#FFFFFF" if QColor(accent).lightness() < 170 else "#000000",
                  window="#F3F3F3", surface="#FFFFFF", surface_alt="#F7F7F7", border="#E5E5E5",
                  input_bg="#FFFFFF", input_border="#D1D1D1", text="#1B1B1B", muted="#5F5F5F",
                  nav_selected="#E9E9E9", ok_fg="#0F7B0F", ok_bg="#DFF6DD", err_fg="#C42B1C", err_bg="#FDE7E9",
                  warn_fg="#8A5300", warn_bg="#FFF4CE", info_fg="#0F548C", info_bg="#EBF3FC")


_platform_palette: Optional[QPalette] = None
current: Optional[Tokens] = None


def _first_family(candidates) -> Optional[str]:
    families = set(QFontDatabase.families())
    return next((f for f in candidates if f in families), None)


def mono_font(size: float = 9.5) -> QFont:
    f = QFont(_first_family(MONO_FONTS) or "monospace")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSizeF(size)
    return f


def _system_accent() -> str:
    pal = _platform_palette or QGuiApplication.palette()
    try:
        c = pal.color(QPalette.ColorRole.Accent)
    except AttributeError:
        c = pal.color(QPalette.ColorRole.Highlight)
    # the platform's own default highlight on non-Windows platforms is not a user choice — use the brand blue
    if not sys.platform.startswith("win") or not c.isValid():
        return FALLBACK_ACCENT
    return c.name()


def _is_dark(scheme: str) -> bool:
    if scheme == "dark":
        return True
    if scheme == "light":
        return False
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def _palette(t: Tokens) -> QPalette:
    p = QPalette()
    c = QColor
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        p.setColor(group, QPalette.ColorRole.Window, c(t.window))
        p.setColor(group, QPalette.ColorRole.WindowText, c(t.text))
        p.setColor(group, QPalette.ColorRole.Base, c(t.input_bg))
        p.setColor(group, QPalette.ColorRole.AlternateBase, c(t.surface_alt))
        p.setColor(group, QPalette.ColorRole.Text, c(t.text))
        p.setColor(group, QPalette.ColorRole.Button, c(t.surface))
        p.setColor(group, QPalette.ColorRole.ButtonText, c(t.text))
        p.setColor(group, QPalette.ColorRole.ToolTipBase, c(t.surface))
        p.setColor(group, QPalette.ColorRole.ToolTipText, c(t.text))
        p.setColor(group, QPalette.ColorRole.Highlight, c(t.accent))
        p.setColor(group, QPalette.ColorRole.HighlightedText, c(t.accent_text))
        p.setColor(group, QPalette.ColorRole.Link, c(t.accent))
        p.setColor(group, QPalette.ColorRole.PlaceholderText, c(t.muted))
        p.setColor(group, QPalette.ColorRole.Mid, c(t.border))
        p.setColor(group, QPalette.ColorRole.Light, c(t.surface))
        p.setColor(group, QPalette.ColorRole.Midlight, c(t.surface_alt))
        p.setColor(group, QPalette.ColorRole.Dark, c(t.input_border))
        try:
            p.setColor(group, QPalette.ColorRole.Accent, c(t.accent))
        except AttributeError:
            pass
    disabled = c(t.muted)
    disabled.setAlpha(140)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, c(t.window))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, c(t.window))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Window, c(t.window))
    return p


def _layout_qss(t: Tokens) -> str:
    """Rules that only target the window's own named widgets (safe on any style)."""
    return f"""
QWidget#Content, QWidget#PageBody {{ background: {t.window}; }}
QScrollArea#PageScroll {{ background: transparent; border: none; }}
QFrame#Card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; }}
QFrame#Card QLabel {{ background: transparent; }}
QLabel#PageTitle {{ font-size: 20pt; font-weight: 600; }}
QLabel#CardTitle {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#Muted, QLabel#Hint {{ color: {t.muted}; }}
QLabel#Hint[state="ok"] {{ color: {t.ok_fg}; }}
QLabel#Hint[state="error"] {{ color: {t.err_fg}; }}
QLabel#AppName {{ font-size: 13pt; font-weight: 600; }}
QLabel#Version {{ color: {t.muted}; font-size: 8.5pt; }}
QLabel#StatValue {{ font-size: 20pt; font-weight: 600; }}
QLabel#StatValue[tone="ok"] {{ color: {t.ok_fg}; }}
QLabel#StatValue[tone="error"] {{ color: {t.err_fg}; }}
QLabel#StatValue[tone="warn"] {{ color: {t.warn_fg}; }}
QLabel#StatLabel {{ color: {t.muted}; }}
QFrame#InfoBar {{ border-radius: 6px; border: 1px solid {t.border}; background: {t.info_bg}; }}
QFrame#InfoBar[tone="ok"] {{ background: {t.ok_bg}; }}
QFrame#InfoBar[tone="error"] {{ background: {t.err_bg}; }}
QFrame#InfoBar[tone="warn"] {{ background: {t.warn_bg}; }}
QFrame#InfoBar QLabel {{ background: transparent; }}
QLabel#InfoTitle {{ font-weight: 600; }}
QFrame#Banner {{ background: {t.info_bg}; border: 1px solid {t.border}; border-radius: 8px; }}
QFrame#Banner QLabel {{ background: transparent; }}
QWidget#Sidebar {{ background: {t.window}; }}
QListWidget#StepNav {{ background: transparent; border: none; outline: none; }}
QListWidget#StepNav::item {{ padding: 9px 10px; margin: 2px 0; border-radius: 6px; color: {t.text}; }}
QListWidget#StepNav::item:hover {{ background: {t.surface_alt}; }}
QListWidget#StepNav::item:selected {{ background: {t.nav_selected}; color: {t.text};
    border-left: 3px solid {t.accent}; }}
QPushButton#Chip {{ border: 1px solid {t.input_border}; border-radius: 13px; padding: 4px 12px;
    background: {t.surface}; }}
QPushButton#Chip:hover {{ border-color: {t.accent}; color: {t.accent}; }}
QPushButton#NavButton {{ text-align: left; padding: 7px 10px; border: none; border-radius: 6px;
    background: transparent; }}
QPushButton#NavButton:hover {{ background: {t.surface_alt}; }}
QPushButton#LinkButton {{ border: none; background: transparent; color: {t.accent}; padding: 0; }}
QPushButton#LinkButton:hover {{ text-decoration: underline; }}
QFrame#LogPanel {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
QPushButton#LogToggle {{ border: none; background: transparent; font-weight: 600; text-align: left;
    padding: 6px 4px; }}
QPlainTextEdit#Log {{ background: {t.surface}; border: none; }}
QLabel#Badge {{ border-radius: 10px; padding: 2px 10px; font-weight: 600; font-size: 8.5pt;
    background: {t.surface_alt}; }}
QLabel#Badge[kind="pass"] {{ color: {t.ok_fg}; background: {t.ok_bg}; }}
QLabel#Badge[kind="fail"] {{ color: {t.err_fg}; background: {t.err_bg}; }}
QLabel#Badge[kind="exception"] {{ color: {t.warn_fg}; background: {t.warn_bg}; }}
QStatusBar {{ background: {t.window}; border-top: 1px solid {t.border}; }}
QStatusBar QLabel {{ padding: 0 6px; }}
"""


def _control_qss(t: Tokens) -> str:
    """The Fluent-like controls (Fusion only; the native style draws its own)."""
    return f"""
QPushButton {{ background: {t.surface}; border: 1px solid {t.input_border}; border-radius: 5px;
    padding: 6px 16px; min-height: 20px; }}
QPushButton:hover {{ background: {t.surface_alt}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.window}; border-color: {t.border}; }}
QPushButton#Primary {{ background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent};
    font-weight: 600; padding: 7px 22px; }}
QPushButton#Primary:hover {{ background: {QColor(t.accent).lighter(110).name()}; }}
QPushButton#Primary:pressed {{ background: {QColor(t.accent).darker(115).name()}; }}
QPushButton#Primary:disabled {{ background: {t.border}; color: {t.muted}; border-color: {t.border}; }}
QLineEdit, QPlainTextEdit#Editor, QComboBox, QSpinBox {{ background: {t.input_bg}; border: 1px solid {t.input_border};
    border-bottom: 1px solid {t.muted}; border-radius: 5px; padding: 5px 8px; selection-background-color: {t.accent};
    selection-color: {t.accent_text}; }}
QLineEdit:focus, QPlainTextEdit#Editor:focus, QComboBox:focus {{ border-bottom: 2px solid {t.accent}; }}
QLineEdit[invalid="true"] {{ border-bottom: 2px solid {t.err_fg}; }}
QTableView {{ background: {t.surface}; alternate-background-color: {t.surface_alt}; border: 1px solid {t.border};
    border-radius: 6px; gridline-color: transparent; selection-background-color: {t.nav_selected};
    selection-color: {t.text}; }}
QHeaderView::section {{ background: {t.surface}; color: {t.muted}; border: none;
    border-bottom: 1px solid {t.border}; padding: 6px 8px; font-weight: 600; }}
QProgressBar {{ border: none; border-radius: 3px; background: {t.border}; max-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.input_border}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.input_border}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
QMenuBar {{ background: {t.window}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {t.surface_alt}; }}
QMenu {{ background: {t.surface}; border: 1px solid {t.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.surface_alt}; color: {t.text}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
QTextBrowser {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 6px; }}
QSplitter::handle {{ background: {t.border}; }}
"""


def apply(app: QApplication, scheme: str = "system", native: bool = False) -> Tokens:
    """(Re)style the whole application. ``scheme``: system | light | dark. Safe to call again (e.g. when Windows
    switches between light and dark)."""
    global _platform_palette, current
    if _platform_palette is None:
        _platform_palette = QPalette(QGuiApplication.palette())
    try:
        app.styleHints().setColorScheme({"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}
                                        .get(scheme, Qt.ColorScheme.Unknown))
    except AttributeError:
        pass
    keys = {k.lower(): k for k in QStyleFactory.keys()}
    forced = os.environ.get(STYLE_ENV, "").strip().lower()
    win11 = sys.platform.startswith("win") and sys.getwindowsversion().build >= 22000 and "windows11" in keys
    style = forced if forced in keys else ("windows11" if native and win11 else "fusion")
    app.setStyle(keys.get(style, "Fusion"))
    t = tokens(_is_dark(scheme), _system_accent())
    family = _first_family(UI_FONTS)
    font = QFont(family) if family else QFont(app.font())
    font.setPointSizeF(10.0)
    app.setFont(font)
    if style == "fusion":
        app.setPalette(_palette(t))
        app.setStyleSheet(_control_qss(t) + _layout_qss(t))
    else:
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet(_layout_qss(t))
    current = t
    return t
