"""regexer - a live regex tester.

type a pattern, matches light up in the sample text, groups get listed,
tokens get explained. runs on plain Qt, no web stuff.

run: py -3.14 regexer.py
"""

import re
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QTextCharFormat, QTextCursor,
                           QSyntaxHighlighter, QIcon, QPixmap, QPainter,
                           QPen)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget,
                               QVBoxLayout, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QTableWidget, QTableWidgetItem,
                               QCheckBox, QSplitter, QLabel, QHeaderView)

BG = "#2b2b2b"
PANEL = "#3a3a3a"
TXT = "#d8d8d8"
ACC = "#e8763e"

SAMPLE = '''order #1024 shipped 2024-03-12
refund #1088 pending 2024-04-01
order #1130 shipped 2024-04-19
note: ping admin@corp.net or ops+alerts@corp.net
ips: 10.0.0.4, 172.16.255.1, 8.8.8.8
'''

TOKEN_INFO = [
    (r"\\d", "digit 0-9"),
    (r"\\w", "word char [a-zA-Z0-9_]"),
    (r"\\s", "whitespace"),
    (r"\\b", "word boundary"),
    (r"\.", "any character"),
    (r"\*", "0 or more"),
    (r"\+", "1 or more"),
    (r"\?", "optional / lazy"),
    (r"\{[^}]*\}", "repeat count"),
    (r"\[[^\]]*\]", "char class"),
    (r"\(([^)]*)\)", None),
    (r"\^", "start of line/string"),
    (r"\$", "end of line/string"),
    (r"\|", "alternation (or)"),
    (r"\\[A-Za-z]", "escaped char"),
]


class HiLite(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.ranges = []

    def highlightBlock(self, text):
        off0 = self.currentBlock().position()
        for a, b, color in self.ranges:
            s = max(a - off0, 0)
            e = min(b - off0, len(text))
            if s < e:
                f = QTextCharFormat()
                f.setBackground(QColor(color))
                f.setForeground(QColor("#fff"))
                self.setFormat(s, e - s, f)


def mk_icon():
    px = QPixmap(32, 32)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#e8763e"), 3))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(4, 4, 16, 16)
    p.setPen(QPen(QColor("#ddd"), 3))
    p.drawLine(19, 19, 28, 28)
    p.end()
    return QIcon(px)


class Win(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("regexer")
        self.setWindowIcon(mk_icon())
        self.resize(980, 700)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(6, 6, 6, 2)
        row = QHBoxLayout()
        row.addWidget(QLabel("pattern"))
        self.pat = QLineEdit(r"(#\d+)\s+(\w+)\s+(\d{4}-\d{2}-\d{2})")
        self.pat.setFont(QFont("Consolas", 11))
        self.pat.setPlaceholderText("type a regex...")
        row.addWidget(self.pat, 1)
        self.flags = {}
        for name, short in [("ignorecase", "i"), ("multiline", "m"),
                            ("dotall", "s"), ("verbose", "x")]:
            cb = QCheckBox(short)
            cb.setChecked(name == "multiline")
            cb.setToolTip(name)
            self.flags[name] = cb
            row.addWidget(cb)
        tl.addLayout(row)

        self._busy = False
        self.msg = QLabel("")
        self.msg.setStyleSheet("color: #d15b5b;")
        tl.addWidget(self.msg)

        self.sample = QPlainTextEdit(SAMPLE)
        self.sample.setFont(QFont("Consolas", 11))
        self.hl = HiLite(self.sample.document())

        right = QSplitter(Qt.Vertical)
        self.groups = QTableWidget(0, 3)
        self.groups.setHorizontalHeaderLabels(["match", "group", "text"])
        self.groups.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.groups.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tokens = QTableWidget(0, 2)
        self.tokens.setHorizontalHeaderLabels(["token", "meaning"])
        self.tokens.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.tokens.setEditTriggers(QTableWidget.NoEditTriggers)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(QLabel("captures"))
        wl.addWidget(self.groups)
        wl.addWidget(QLabel("what the pattern does"))
        wl.addWidget(self.tokens)
        right.addWidget(wrap)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.sample)
        split.addWidget(right)
        split.setSizes([560, 400])

        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(top)
        ml.addWidget(split)
        self.setCentralWidget(mid)

        self.pat.textChanged.connect(self._refresh)
        self.sample.textChanged.connect(self._refresh)
        for cb in self.flags.values():
            cb.toggled.connect(self._refresh)
        self._refresh()
        self.statusBar().showMessage("ready")

    def closeEvent(self, e):
        self.hl.setDocument(None)
        super().closeEvent(e)

    def _flagval(self):
        v = 0
        for name, flag in [("ignorecase", re.IGNORECASE),
                           ("multiline", re.MULTILINE),
                           ("dotall", re.DOTALL),
                           ("verbose", re.VERBOSE)]:
            if self.flags[name].isChecked():
                v |= flag.value
        return v

    def _refresh(self):
        if self._busy:
            return
        self._busy = True
        self._refresh_inner()
        self._busy = False

    def _refresh_inner(self):
        pat = self.pat.text()
        text = self.sample.toPlainText()
        self.hl.ranges = []
        self.groups.setRowCount(0)
        self.tokens.setRowCount(0)
        if not pat:
            self.hl.rehighlight()
            self.msg.setText("")
            return
        try:
            rx = re.compile(pat, self._flagval())
        except re.error as e:
            self.msg.setText(str(e))
            self.hl.rehighlight()
            return
        self.msg.setText("")
        colors = ["#5a6b3f", "#3f5a6b", "#6b3f5a", "#6b5a3f"]
        ms = list(rx.finditer(text))
        for i, m in enumerate(ms):
            self.hl.ranges.append((m.start(), m.end(),
                                   colors[i % len(colors)]))
            row = self.groups.rowCount()
            self.groups.insertRow(row)
            self.groups.setItem(row, 0, QTableWidgetItem(str(i)))
            self.groups.setItem(row, 1, QTableWidgetItem("whole"))
            self.groups.setItem(row, 2, QTableWidgetItem(m.group(0)[:60]))
            for g in range(1, len(m.groups()) + 1):
                row = self.groups.rowCount()
                self.groups.insertRow(row)
                self.groups.setItem(row, 0, QTableWidgetItem(""))
                self.groups.setItem(
                    row, 1, QTableWidgetItem("group %d" % g))
                self.groups.setItem(
                    row, 2, QTableWidgetItem(str(m.group(g))[:60]))
        self.statusBar().showMessage("%d match(es)" % len(ms))
        self.hl.rehighlight()
        self._explain(pat)

    def _explain(self, pat):
        i = 0
        rows = []
        while i < len(pat):
            hit = None
            for tok, desc in TOKEN_INFO:
                m = re.match(tok, pat[i:])
                if m:
                    hit = (m.group(0), desc)
                    break
            if hit:
                tok_txt, desc = hit
                if desc is None:
                    if tok_txt.startswith("("):
                        inner = tok_txt[1:-1] if tok_txt.endswith(")") \
                            else tok_txt[1:]
                        desc = "capture group: " + inner[:30]
                rows.append((tok_txt, desc))
                i += len(tok_txt)
            else:
                j = i
                while j < len(pat) and not any(
                        re.match(t, pat[j:]) for t, _ in TOKEN_INFO):
                    j += 1
                lit = pat[i:j]
                rows.append((lit, "literal text"))
                i = j
        for tok_txt, desc in rows:
            row = self.tokens.rowCount()
            self.tokens.insertRow(row)
            self.tokens.setItem(row, 0, QTableWidgetItem(tok_txt))
            self.tokens.setItem(row, 1, QTableWidgetItem(desc))


def launch():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QMainWindow, QWidget { background: %s; color: %s; }
        QLineEdit, QPlainTextEdit, QTableWidget {
            background: %s; color: %s; border: 1px solid #222;
            selection-background-color: #5a6b7d; }
        QHeaderView::section { background: %s; color: %s;
            border: 0; padding: 4px; }
        QCheckBox { color: %s; }
        QLabel { color: %s; }
        QStatusBar { background: #007acc; color: #fff; }
        QSplitter::handle { background: #222; }
        QTableWidget { gridline-color: #333; }
    """ % (PANEL, TXT, BG, TXT, PANEL, TXT, TXT, TXT))
    w = Win()
    w.show()
    if "--shot" in sys.argv:
        def snap():
            w.grab().toImage().save("shot.png")
            app.quit()
        QTimer.singleShot(2500, snap)
    app.exec()


if __name__ == "__main__":
    launch()
