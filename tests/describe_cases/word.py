"""Describe cases: one of Obliterate's named Word actions. The sentence names the action by the menu's own words,
says what Word does to the copy, and that the result is shown, not proven; the action's identifier never appears."""
from __future__ import annotations

CASES = {
    "word_action": [
        ({}, {"action": "unlink_fields"},
         ["Word action, Unlink fields", "Word replaces every field in the body", "Fields in headers and footers stay "
          "live", "Not tracked", "shown, not proven"]),
        ({}, {"action": "accept_changes"},
         ["Word action, Accept all tracked changes", "Word accepts every tracked change in the copy"]),
        ({}, {"action": "delete_shapes"},
         ["Word action, Delete drawn shapes", "Pictures, charts and embedded objects stay"]),
        ({}, {"action": "repair"},
         ["Word action, Open and repair, then save", "Open and Repair command"]),
    ],
}
