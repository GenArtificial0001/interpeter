"""Diálogo para editar el glosario: términos en inglés, siglas y nombres que no se deben estropear."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..glossary import Glossary, GlossaryEntry

log = logging.getLogger(__name__)

COLUMNS = [
    "Término",
    "Variantes que oye el reconocimiento",
    "Pronunciación",
    "Inglés",
    "Portugués",
    "Francés",
    "Alemán",
]
TARGETS = {3: "en", 4: "pt", 5: "fr", 6: "de"}
HELP = (
    "Cada fila es un término que NO debe traducirse ni deformarse (SharePoint, Ford, SAP…).\n"
    "• Variantes: cómo lo escribe a veces el reconocimiento («sherpoin, share point»), separadas por comas. "
    "Para una sigla, cómo suena en español («ese a pe»). Mira el registro si dudas.\n"
    "• Pronunciación: cómo debe decirlo la voz sintética («S A P» para deletrear). Vacío = tal cual.\n"
    "• Inglés / Portugués / Francés / Alemán: si quieres una traducción fija (p. ej. «patio de "
    "contenedores» → «container yard»). Vacío = se deja el término igual."
)


def _csv(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


class GlossaryDialog(QDialog):
    def __init__(self, parent: QWidget | None, glossary: Glossary, path: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("Glosario de términos")
        self.setMinimumSize(820, 460)
        self._glossary, self._path = glossary, path
        self.saved = False

        lay = QVBoxLayout(self)
        intro = QLabel(HELP)
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        lay.addWidget(intro)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for col, width in enumerate((140, 220, 100, 110, 110, 110, 110)):
            self.table.setColumnWidth(col, width)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_add = QPushButton("Agregar término")
        self.btn_del = QPushButton("Quitar seleccionado")
        self.btn_defaults = QPushButton("Restaurar ejemplos")
        self.btn_save = QPushButton("Guardar")
        self.btn_save.setObjectName("primary")
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_add.clicked.connect(lambda: self.add_row())
        self.btn_del.clicked.connect(self._remove_selected)
        self.btn_defaults.clicked.connect(self._restore_defaults)
        self.btn_save.clicked.connect(self._save)
        self.btn_cancel.clicked.connect(self.reject)
        for b in (self.btn_add, self.btn_del, self.btn_defaults):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_save)
        lay.addLayout(row)

        self._fill(glossary.entries)

    # -- tabla <-> entradas ------------------------------------------------------------------------

    def _fill(self, entries: list[GlossaryEntry]) -> None:
        self.table.setRowCount(0)
        for e in entries:
            self.add_row(e)

    def add_row(self, entry: GlossaryEntry | None = None) -> int:
        r = self.table.rowCount()
        self.table.insertRow(r)
        e = entry or GlossaryEntry("")
        cells = [e.term, ", ".join(e.aliases), e.spoken]
        cells += [e.translations.get(code, "") for code in TARGETS.values()]
        for c, text in enumerate(cells):
            self.table.setItem(r, c, QTableWidgetItem(text))
        if entry is None:
            self.table.setCurrentCell(r, 0)
            first = self.table.item(r, 0)
            if first is not None:
                self.table.editItem(first)
        return r

    def _text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item else ""

    def entries(self) -> list[GlossaryEntry]:
        """Entradas válidas de la tabla (se ignoran las filas sin término y los términos repetidos)."""
        out: list[GlossaryEntry] = []
        seen: set[str] = set()
        for r in range(self.table.rowCount()):
            term = self._text(r, 0)
            if not term or term.casefold() in seen:
                continue
            seen.add(term.casefold())
            translations = {code: self._text(r, col) for col, code in TARGETS.items() if self._text(r, col)}
            out.append(GlossaryEntry(term, _csv(self._text(r, 1)), self._text(r, 2), translations))
        return out

    def _remove_selected(self) -> None:
        for idx in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(idx)

    def _restore_defaults(self) -> None:
        self._fill(Glossary.defaults().entries)

    def _save(self) -> None:
        new = Glossary(self.entries())
        try:
            new.save(self._path)
        except OSError as exc:
            log.error("No se pudo guardar el glosario", exc_info=exc)
            QMessageBox.warning(self, "Glosario", f"No se pudo guardar el glosario:\n{exc}")
            return
        self._glossary.replace_with(new)  # las etapas del motor usan este mismo objeto: aplica de inmediato
        self.saved = True
        self.accept()
