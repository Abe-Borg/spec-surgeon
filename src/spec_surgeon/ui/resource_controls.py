"""Shared investigation resource settings; values are read on the GUI thread before work starts."""
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QSpinBox, QWidget


class ResourceControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        from .. import resources
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.cost = QDoubleSpinBox(self)
        self.cost.setRange(0.01, 1000000)
        self.cost.setPrefix("$ ")
        self.cost.setValue(resources.DEFAULT_MAX_COST_USD)
        self.cost.setToolTip("Estimated spending allocation for one investigation; saved work survives a pause.")
        form.addRow("Estimated spending allocation", self.cost)
        self.attempts = self._integer(form, "Attempts per group", 10000, resources.DEFAULT_MAX_ATTEMPTS)
        self.calls = self._integer(form, "Model calls", 100000, resources.DEFAULT_MAX_CALLS)
        self.output_tokens = self._integer(form, "Output tokens per call", 64000, resources.DEFAULT_MAX_OUTPUT_TOKENS)
        self.total_tokens = self._integer(form, "Total input + output tokens", 1000000000,
                                          resources.DEFAULT_MAX_TOTAL_TOKENS)
        self.effort = QComboBox(self)
        self.effort.addItems(["low", "medium", "high", "max"])
        self.effort.setCurrentText(resources.DEFAULT_EFFORT)
        form.addRow("Reasoning effort", self.effort)

    def _integer(self, form, title, maximum, value):
        widget = QSpinBox(self)
        widget.setRange(1, maximum)
        widget.setGroupSeparatorShown(True)
        widget.setValue(value)
        form.addRow(title, widget)
        return widget

    def values(self):
        return dict(max_cost_usd=self.cost.value(), max_attempts=self.attempts.value(),
                    max_calls=self.calls.value(), max_output_tokens=self.output_tokens.value(),
                    max_total_tokens=self.total_tokens.value(), effort=self.effort.currentText())

    def set_values(self, options):
        """Restore saved allowances without silently replacing them with new-session defaults."""
        for key, control in (("max_cost_usd", self.cost), ("max_attempts", self.attempts),
                             ("max_calls", self.calls), ("max_output_tokens", self.output_tokens),
                             ("max_total_tokens", self.total_tokens)):
            value = options.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                if value > control.maximum():
                    control.setMaximum(value)
                control.setValue(value)
        if options.get("effort") in ("low", "medium", "high", "max"):
            self.effort.setCurrentText(options["effort"])
