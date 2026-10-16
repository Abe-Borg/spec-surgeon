"""The window's look: Spec Surgeon's own light/dark theme on Qt's Fusion style, the same on every Windows version
(and in the screenshots and tests, which run on Linux).

- Colours follow Windows' light/dark setting; the accent is the Windows accent colour when Qt reports one.
  ``SPEC_SURGEON_THEME=light|dark`` forces a scheme (screenshots, tests). ``SPEC_SURGEON_QT_STYLE`` forces a Qt
  style — diagnostics only: the controls below are drawn for Fusion, another style gets only the layout rules.
- A dark navy sidebar carries the four steps (``widgets.StepDelegate`` paints the rail from these tokens); the
  content area is light with white cards, a white action bar at the foot of every step, and status pills.
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
THEME_ENV = "SPEC_SURGEON_THEME"
FALLBACK_ACCENT = "#1F6FEB"
UI_FONTS = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans")
MONO_FONTS = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono")


@dataclass(frozen=True)
class Tokens:
    dark: bool
    accent: str
    accent_text: str
    window: str
    surface: str          # cards, the action bar, the log
    surface_alt: str      # table alternate rows, chips, drop zone
    border: str
    input_bg: str
    input_border: str
    text: str
    muted: str
    selection: str        # table selection
    ok_fg: str
    ok_bg: str
    err_fg: str
    err_bg: str
    warn_fg: str
    warn_bg: str
    info_fg: str
    info_bg: str
    sidebar: str          # the step rail
    sidebar_text: str
    sidebar_muted: str
    sidebar_hover: str
    sidebar_selected: str
    sidebar_border: str
    rail: str             # the line joining the step circles


def tokens(dark: bool, accent: str) -> Tokens:
    accent_text = "#000000" if QColor(accent).lightness() > 160 else "#FFFFFF"
    if dark:
        return Tokens(True, accent, accent_text,
                      window="#171B21", surface="#1F252D", surface_alt="#262D36", border="#313A45",
                      input_bg="#1A2027", input_border="#3D4754", text="#EEF2F7", muted="#9FACBC",
                      selection="#2A3542", ok_fg="#5BD47F", ok_bg="#17311F", err_fg="#FF8F86", err_bg="#3B1D1B",
                      warn_fg="#F5C451", warn_bg="#3A2D12", info_fg="#8FBFFF", info_bg="#1B2E4A",
                      sidebar="#10141A", sidebar_text="#EEF2F7", sidebar_muted="#8794A5", sidebar_hover="#1A2028",
                      sidebar_selected="#232B35", sidebar_border="#1F2730", rail="#303A47")
    return Tokens(False, accent, accent_text,
                  window="#F4F6F9", surface="#FFFFFF", surface_alt="#F1F4F8", border="#DFE4EB",
                  input_bg="#FFFFFF", input_border="#C6CFDA", text="#172033", muted="#5B6878",
                  selection="#E6ECF5", ok_fg="#167C3D", ok_bg="#DDF5E4", err_fg="#B42318", err_bg="#FDE4E1",
                  warn_fg="#8A5A00", warn_bg="#FFF1C9", info_fg="#1D4F91", info_bg="#E3EEFB",
                  sidebar="#1B2431", sidebar_text="#F3F6FA", sidebar_muted="#9AA8BA", sidebar_hover="#26303F",
                  sidebar_selected="#2D3A4B", sidebar_border="#2A3544", rail="#3B4858")


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


def scheme() -> str:
    """``light`` or ``dark``: the environment's choice (``SPEC_SURGEON_THEME``) when it names one, else Windows'."""
    forced = os.environ.get(THEME_ENV, "").strip().lower()
    if forced in ("light", "dark"):
        return forced
    try:
        return "dark" if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
    except AttributeError:
        return "light"


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
QWidget#Content, QWidget#PageBody, QWidget#PageHeader {{ background: {t.window}; }}
QScrollArea#PageScroll {{ background: transparent; border: none; }}
QWidget#Footer {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
QWidget#Footer QLabel {{ background: transparent; }}
QFrame#Card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}
QFrame#Card QLabel {{ background: transparent; }}
QFrame#StatTile {{ background: {t.surface}; border: 1px solid {t.border}; border-top: 3px solid {t.input_border};
    border-radius: 10px; }}
QFrame#StatTile[tone="ok"] {{ border-top-color: {t.ok_fg}; }}
QFrame#StatTile[tone="error"] {{ border-top-color: {t.err_fg}; }}
QFrame#StatTile[tone="warn"] {{ border-top-color: {t.warn_fg}; }}
QFrame#StatTile[tone="accent"] {{ border-top-color: {t.accent}; }}
QFrame#StatTile QLabel {{ background: transparent; }}
QLabel#Eyebrow {{ color: {t.accent}; }}
QLabel#PageTitle {{ font-size: 19pt; font-weight: 600; }}
QLabel#CardTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#Muted, QLabel#Hint {{ color: {t.muted}; }}
QLabel#Hint[state="ok"] {{ color: {t.ok_fg}; }}
QLabel#Hint[state="error"] {{ color: {t.err_fg}; }}
QLabel#StatValue {{ font-size: 20pt; font-weight: 600; }}
QLabel#StatValue[tone="ok"] {{ color: {t.ok_fg}; }}
QLabel#StatValue[tone="error"] {{ color: {t.err_fg}; }}
QLabel#StatValue[tone="warn"] {{ color: {t.warn_fg}; }}
QLabel#StatLabel {{ color: {t.muted}; }}
QFrame#InfoBar {{ border-radius: 6px; border: 1px solid {t.border}; border-left: 4px solid {t.info_fg};
    background: {t.info_bg}; }}
QFrame#InfoBar[tone="ok"] {{ background: {t.ok_bg}; border-left-color: {t.ok_fg}; }}
QFrame#InfoBar[tone="error"] {{ background: {t.err_bg}; border-left-color: {t.err_fg}; }}
QFrame#InfoBar[tone="warn"] {{ background: {t.warn_bg}; border-left-color: {t.warn_fg}; }}
QFrame#InfoBar QLabel {{ background: transparent; }}
QLabel#InfoTitle {{ font-weight: 600; }}
QFrame#Banner {{ background: {t.info_bg}; border: 1px solid {t.border}; border-left: 4px solid {t.accent};
    border-radius: 8px; }}
QFrame#Banner QLabel {{ background: transparent; }}
QFrame#EmptyState {{ background: {t.surface}; border: 1px dashed {t.input_border}; border-radius: 10px; }}
QFrame#EmptyState QLabel {{ background: transparent; }}
QLabel#EmptyTitle {{ font-size: 12pt; font-weight: 600; }}
QFrame#DropZone {{ background: {t.surface_alt}; border: 2px dashed {t.input_border}; border-radius: 10px; }}
QFrame#DropZone[active="true"] {{ background: {t.info_bg}; border-color: {t.accent}; }}
QFrame#DropZone QLabel {{ background: transparent; }}
QLabel#DropTitle {{ font-size: 11pt; font-weight: 600; }}
QWidget#Sidebar {{ background: {t.sidebar}; }}
QWidget#Sidebar QLabel {{ color: {t.sidebar_text}; background: transparent; }}
QLabel#AppName {{ font-size: 13pt; font-weight: 600; }}
QLabel#Version {{ color: {t.sidebar_muted}; font-size: 8.5pt; }}
QLabel#SideHeading {{ color: {t.sidebar_muted}; font-size: 8pt; font-weight: 600; }}
QFrame#SidebarDivider {{ background: {t.sidebar_border}; border: none; max-height: 1px; min-height: 1px; }}
QListWidget#StepNav {{ background: transparent; border: none; outline: none; }}
QPushButton#NavButton {{ color: {t.sidebar_text}; text-align: left; padding: 8px 12px; border: none;
    border-radius: 6px; background: transparent; }}
QPushButton#NavButton:hover {{ background: {t.sidebar_hover}; }}
QPushButton#NavButton:pressed {{ background: {t.sidebar_selected}; }}
QPushButton#NavButton:disabled {{ color: {t.sidebar_muted}; background: transparent; }}
QPushButton#Chip {{ border: 1px solid {t.input_border}; border-radius: 14px; padding: 5px 13px;
    background: {t.surface_alt}; color: {t.text}; }}
QPushButton#Chip:hover {{ border-color: {t.accent}; color: {t.accent}; background: {t.info_bg}; }}
QPushButton#LinkButton {{ border: none; background: transparent; color: {t.accent}; padding: 0; }}
QPushButton#LinkButton:hover {{ text-decoration: underline; }}
QLabel#Pill {{ border-radius: 10px; padding: 3px 10px; font-size: 8.5pt; font-weight: 600;
    background: {t.surface_alt}; color: {t.muted}; }}
QLabel#Pill[tone="ok"] {{ background: {t.ok_bg}; color: {t.ok_fg}; }}
QLabel#Pill[tone="warn"] {{ background: {t.warn_bg}; color: {t.warn_fg}; }}
QLabel#Pill[tone="info"] {{ background: {t.info_bg}; color: {t.info_fg}; }}
QFrame#LogPanel {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
QPushButton#LogToggle {{ border: none; background: transparent; font-weight: 600; text-align: left;
    padding: 6px 4px; }}
QPlainTextEdit#Log {{ background: {t.surface}; border: none; }}
QLabel#Badge {{ border-radius: 10px; padding: 2px 10px; font-weight: 600; font-size: 8.5pt;
    background: {t.surface_alt}; }}
QLabel#Badge[kind="pass"] {{ color: {t.ok_fg}; background: {t.ok_bg}; }}
QLabel#Badge[kind="fail"] {{ color: {t.err_fg}; background: {t.err_bg}; }}
QLabel#Badge[kind="exception"] {{ color: {t.warn_fg}; background: {t.warn_bg}; }}
QFrame#ChatMsg {{ border-radius: 8px; border: 1px solid {t.border}; background: {t.surface_alt}; }}
QFrame#ChatMsg[role="user"] {{ background: {t.info_bg}; }}
QFrame#ChatMsg[role="app"] {{ background: {t.surface}; border-style: dashed; }}
QFrame#ChatMsg QLabel {{ background: transparent; }}
QLabel#ChatWho {{ font-weight: 600; font-size: 8.5pt; color: {t.muted}; }}
QFrame#ChoiceRow {{ border-radius: 6px; border: 1px solid {t.input_border}; background: {t.surface}; }}
QFrame#ChoiceRow QLabel {{ background: transparent; }}
QDialog#About QLabel#AboutName {{ font-size: 16pt; font-weight: 600; }}
QTextBrowser#AboutBody {{ background: transparent; border: none; }}
QStatusBar {{ background: {t.window}; border-top: 1px solid {t.border}; }}
QStatusBar QLabel {{ padding: 0 6px; }}
"""


def _control_qss(t: Tokens) -> str:
    """The Fluent-like controls (Fusion only; another style draws its own)."""
    return f"""
QPushButton {{ background: {t.surface}; border: 1px solid {t.input_border}; border-radius: 6px;
    padding: 6px 16px; min-height: 20px; }}
QPushButton:hover {{ background: {t.surface_alt}; border-color: {t.muted}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.window}; border-color: {t.border}; }}
QPushButton#Primary {{ background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent};
    font-weight: 600; padding: 7px 22px; }}
QPushButton#Primary:hover {{ background: {QColor(t.accent).lighter(112).name()};
    border-color: {QColor(t.accent).lighter(112).name()}; }}
QPushButton#Primary:pressed {{ background: {QColor(t.accent).darker(115).name()}; }}
QPushButton#Primary:disabled {{ background: {t.border}; color: {t.muted}; border-color: {t.border}; }}
QLineEdit, QPlainTextEdit#Editor, QComboBox, QSpinBox {{ background: {t.input_bg}; border: 1px solid {t.input_border};
    border-radius: 6px; padding: 6px 9px; selection-background-color: {t.accent}; selection-color: {t.accent_text}; }}
QLineEdit:focus, QPlainTextEdit#Editor:focus, QComboBox:focus {{ border: 1px solid {t.accent}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {t.err_fg}; }}
QListWidget {{ background: {t.input_bg}; border: 1px solid {t.border}; border-radius: 6px; padding: 2px; }}
QListWidget::item {{ padding: 3px 6px; }}
QTableView {{ background: {t.surface}; alternate-background-color: {t.surface_alt}; border: 1px solid {t.border};
    border-radius: 6px; gridline-color: transparent; selection-background-color: {t.selection};
    selection-color: {t.text}; }}
QHeaderView::section {{ background: {t.surface}; color: {t.muted}; border: none;
    border-bottom: 1px solid {t.border}; padding: 7px 8px; font-weight: 600; }}
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
QMenuBar {{ background: {t.window}; border-bottom: 1px solid {t.border}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {t.surface_alt}; }}
QMenu {{ background: {t.surface}; border: 1px solid {t.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.surface_alt}; color: {t.text}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
QTextBrowser {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 6px; }}
QSplitter::handle {{ background: {t.border}; }}
"""


def apply(app: QApplication) -> Tokens:
    """(Re)style the whole application for the current scheme (Windows' light/dark, or ``SPEC_SURGEON_THEME``).
    Safe to call again, e.g. when Windows switches between light and dark."""
    global _platform_palette, current
    if _platform_palette is None:
        _platform_palette = QPalette(QGuiApplication.palette())
    keys = {k.lower(): k for k in QStyleFactory.keys()}
    forced = os.environ.get(STYLE_ENV, "").strip().lower()
    style = forced if forced in keys else "fusion"
    app.setStyle(keys.get(style, "Fusion"))
    t = tokens(scheme() == "dark", _system_accent())
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
