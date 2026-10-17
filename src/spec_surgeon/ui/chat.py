"""The conversation with Claude in the window: Claude's messages and what it understood, the user's replies, the app's
notes (what a preview found), the choices Claude offers for the current turn, a reply box and the running cost.

Layout and signals only — ``ui.app.MainWindow`` decides what a reply or a choice does (``gui.Session.reply``). Two
instances show the same transcript: one on the Describe step, a compact one under the preview. Choice buttons are
rebuilt every turn, so they are never put in the window's static ``job_buttons``; ``set_busy`` disables them.
A choice that carries exact words shows them, one line each, built here from the choice's ``words`` (never from
Claude's label), with "choosing approves these exact words": picking it is how wording Claude suggests — or the words
one of your recipes places — becomes approved. A library choice's button reads "Use it" (a recipe), "Try it" (one
of your operations, tried on the sample files first) or "Insert it" (one of your snippets: the pick is a reply, and
Claude writes where it goes).
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .widgets import Card, Thumb, label, link_button, repolish

ROLE_TITLES = {"user": "You", "claude": "Claude", "app": "Spec Surgeon"}


def _plain(text: str, name: str = "", selectable: bool = True) -> QLabel:
    """Claude's words are shown as plain text — never as rich text or HTML."""
    lab = label(text, name, selectable=selectable)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    return lab


class ChatView(Card):
    send = Signal(str)            # the text typed in this view's reply box
    chosen = Signal(str)          # a choice id
    start_over = Signal()
    image_clicked = Signal(str)

    def __init__(self, compact: bool = False, parent=None):
        super().__init__("Talk it through with Claude",
                         "Claude's questions and your replies. Your own words — and the exact words of a choice you "
                         "pick — are the only words it may put in your documents.", parent)
        self.compact = compact
        self.thread_box = QWidget()
        self.thread = QVBoxLayout(self.thread_box)
        self.thread.setContentsMargins(0, 0, 0, 0)
        self.thread.setSpacing(8)
        self.add(self.thread_box)
        self.choices_box = QWidget()
        self.choices = QVBoxLayout(self.choices_box)
        self.choices.setContentsMargins(0, 0, 0, 0)
        self.choices.setSpacing(6)
        self.add(self.choices_box)
        self.hint = _plain("", "Muted", selectable=False)
        self.hint.hide()
        self.add(self.hint)
        self.reply = QPlainTextEdit()
        self.reply.setObjectName("Editor")
        self.reply.setPlaceholderText("Reply to Claude in your own words — Ctrl+Enter sends")
        self.reply.setMinimumHeight(56)
        self.reply.setMaximumHeight(96)
        self.add(self.reply)
        row = QHBoxLayout()
        self.cost = label("", "Muted", wrap=False)
        self.start_over_btn = link_button("Start over")
        self.start_over_btn.setToolTip("Forget this conversation and start again from the request above")
        self.send_btn = QPushButton("Send")
        self.send_btn.setToolTip("Send your reply to Claude (Ctrl+Enter)")
        row.addWidget(self.cost)
        row.addStretch(1)
        row.addWidget(self.start_over_btn)
        row.addWidget(self.send_btn)
        self.add(row)
        self.send_btn.clicked.connect(self._on_send)
        self.start_over_btn.clicked.connect(self._on_start_over)
        self._choice_buttons: List[QPushButton] = []
        self._busy = False
        self._blocked = ""
        self.hide()

    # ---- content ----
    def set_transcript(self, entries: List[Dict[str, Any]], choices: List[Dict[str, Any]], cost: str,
                       waiting: bool) -> None:
        self._clear(self.thread)
        shown = entries[-6:] if self.compact else entries
        if self.compact and len(entries) > len(shown):
            self.thread.addWidget(_plain(f"… {len(entries) - len(shown)} earlier message(s) — see the Describe step",
                                         "Muted", selectable=False))
        for e in shown:
            self._add_now(self.thread, self._bubble(e))
        self._clear(self.choices)
        self._choice_buttons = []
        for c in choices:
            self._add_now(self.choices, self._choice_row(c))
        self.cost.setText(f"Claude, this request: {cost}" if cost else "")
        self.reply.setPlaceholderText("Reply to Claude in your own words — Ctrl+Enter sends" if waiting else
                                      "Want something different? Tell Claude here — Ctrl+Enter sends")
        self._apply_enabled()
        self.show()

    def set_busy(self, on: bool) -> None:
        self._busy = on
        self._apply_enabled()

    def set_blocked(self, reason: str) -> None:
        """Disable replying (with the reason shown) — e.g. while the request above no longer matches the conversation."""
        self._blocked = reason
        self.hint.setText(reason)
        self.hint.setVisible(bool(reason))
        self._apply_enabled()

    def choice_buttons(self) -> List[QPushButton]:
        return list(self._choice_buttons)

    # ---- internals ----
    def _apply_enabled(self) -> None:
        on = not self._busy and not self._blocked
        for w in [self.send_btn, self.reply, *self._choice_buttons]:
            w.setEnabled(on)
        self.start_over_btn.setEnabled(not self._busy)

    @staticmethod
    def _add_now(layout, w: QWidget) -> None:
        """Add and show at once (a layout would show it later, from the event loop), so the window can lay the page
        out and scroll to the reply box right after a turn."""
        layout.addWidget(w)
        w.show()

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _bubble(self, e: Dict[str, Any]) -> QWidget:
        f = QFrame()
        f.setObjectName("ChatMsg")
        f.setProperty("role", e.get("role", "app"))
        repolish(f)
        v = QVBoxLayout(f)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)
        v.addWidget(_plain(ROLE_TITLES.get(e.get("role"), "Spec Surgeon"), "ChatWho", selectable=False))
        if e.get("understanding") and e.get("role") == "claude":
            v.addWidget(_plain(f"I understood: {e['understanding']}", "Muted"))
        if e.get("text"):
            v.addWidget(_plain(e["text"]))
        for img in e.get("images") or []:
            if Path(img).is_file():
                t = Thumb(Path(img))
                t.clicked.connect(self._on_image)
                v.addWidget(t)
        return f

    def _choice_row(self, c: Dict[str, Any]) -> QWidget:
        f = QFrame()
        f.setObjectName("ChoiceRow")
        h = QHBoxLayout(f)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(10)
        text = c.get("label", "")
        for w in c.get("words") or ([c["wording"]] if c.get("wording") else []):
            text += (f"\nChoosing approves only this occurrence: «{w}»." if c.get("kind") == "capitalization"
                     else f"\nExact words: «{w}» — choosing approves these exact words for your documents.")
        lab = _plain(text, selectable=True)
        h.addWidget(lab, 1)
        b = QPushButton({"custom_transform": "Do this", "recipe": "Use it", "operation": "Try it",
                         "snippet": "Insert it", "capitalization": "Approve this change"}.get(c.get("kind"), "Choose"))
        b.setProperty("choice_id", c.get("id"))
        detail = c.get("detail") or ""        # Claude's words: a tooltip renders what looks like markup as rich text,
        b.setToolTip(f"<p style='white-space:pre-wrap'>{html.escape(detail)}</p>" if detail else "")   # so escape it
        b.clicked.connect(self._on_choice)
        h.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        self._choice_buttons.append(b)
        return f

    @Slot()
    def _on_send(self) -> None:
        text = self.reply.toPlainText().strip()
        if text:
            self.send.emit(text)

    @Slot()
    def _on_choice(self) -> None:
        cid = self.sender().property("choice_id")
        if cid:
            self.chosen.emit(str(cid))

    @Slot()
    def _on_start_over(self) -> None:
        self.start_over.emit()

    @Slot(str)
    def _on_image(self, path: str) -> None:
        self.image_clicked.emit(path)

    def clear_reply(self) -> None:
        self.reply.clear()


def transcript(conversation) -> List[Dict[str, Any]]:
    return [e.to_dict() for e in conversation.entries] if conversation is not None else []


def current_choices(session) -> List[Dict[str, Any]]:
    """The choices still open: those of the latest turn, while it has produced neither a spec nor a transform."""
    turn = getattr(session, "turn", None)
    if turn is None or session.spec is not None or session.generated is not None:
        return []
    return [c.to_dict() for c in turn.choices]


def waiting_for_user(session) -> bool:
    turn = getattr(session, "turn", None)
    return turn is not None and session.spec is None and session.generated is None


def cost_line(conversation) -> Optional[str]:
    return conversation.usage.line() if conversation is not None and conversation.usage.calls else ""
