"""Table models for the Preview and Results steps (rows come from ``ui.rows``)."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from .rows import Row
from .widgets import BADGE_ROLE

ROW_ROLE = Qt.ItemDataRole.UserRole + 2


class RowsModel(QAbstractTableModel):
    def __init__(self, columns: List[str], parent=None):
        super().__init__(parent)
        self.columns = list(columns)
        self.rows: List[Row] = []

    def set_rows(self, rows: List[Row]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return row.cells[col] if col < len(row.cells) else ""
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.tooltip or (row.cells[col] if col < len(row.cells) else None)
        if role == BADGE_ROLE and col == row.status_col:
            return row.badge
        if role == ROW_ROLE:
            return row
        return None

    def badges(self) -> List[str]:
        return [r.badge for r in self.rows]


class ProblemsFilter(QSortFilterProxyModel):
    """"Show problems only": exceptions and failures."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.only_problems = False

    def set_only_problems(self, on: bool) -> None:
        self.only_problems = bool(on)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self.only_problems:
            return True
        return self.sourceModel().rows[source_row].is_problem
