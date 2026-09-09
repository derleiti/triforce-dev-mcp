#!/usr/bin/env python3
"""PyQt6 TriForce Control Center.

The GUI is intentionally a systemd client, never the owner of the backend
process. Long-running checks and privileged operations use QProcess/threads so
the UI event loop remains responsive.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.config import VERSION, get_settings
from app.settings_store import (
    SECRET_ENV_KEYS, ConfigConflict, effective_environment, load_snapshot,
    redact, save_updates, settings_inventory,
)
from app.setup_tasks import TASKS, run_check

APP_NAME = "TriForce Control Center"
ADMIN_HELPER = Path("/usr/lib/triforce/triforce-admin-helper")
DESKTOP_FILE = Path("/usr/share/applications/triforce-control-center.desktop")
AUTOSTART_FILE = Path.home() / ".config/autostart/triforce-control-center.desktop"
MASK = "••••••••"


class WorkerSignals(QObject):
    done = pyqtSignal(object)
    error = pyqtSignal(str)


class Worker(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.done.emit(self.fn(*self.args))
        except Exception as exc:
            self.signals.error.emit(str(exc))


def run_cmd(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    cp = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    return cp.returncode, cp.stdout, cp.stderr


def service_info() -> dict[str, str]:
    rc, out, err = run_cmd([
        "systemctl", "show", "triforce.service", "--no-pager",
        "--property=LoadState,ActiveState,SubState,UnitFileState,MainPID,ExecMainStatus",
    ])
    result = {"error": err.strip()} if rc else {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1); result[key] = value
    return result


def api_health() -> dict[str, object]:
    settings = get_settings()
    host = settings.server_host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    url = f"http://{host}:{settings.server_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
            return {"ok": response.status == 200, "url": url, "status": response.status, "payload": payload}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def _redact_log_text(text: str) -> str:
    try:
        values = load_snapshot().values
        for key in SECRET_ENV_KEYS:
            value = values.get(key)
            if value and len(value) >= 4:
                text = text.replace(value, "***")
    except (OSError, PermissionError):
        pass
    # Defensive masking for common authorization formats.
    import re
    text = re.sub(r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s]+", r"\1***", text)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)[^\s,;]+", r"\1\2***", text)
    return text


def recent_logs(lines: int = 200) -> str:
    rc, out, err = run_cmd(["journalctl", "-u", "triforce.service", "-n", str(lines), "--no-pager", "-o", "short-iso"], 8)
    return _redact_log_text(out if rc == 0 else err)


class OverviewPage(QWidget):
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        title = QLabel("TriForce 2.85 Beta 1")
        title.setFont(QFont(title.font().family(), 20, 600))
        layout.addWidget(title)
        self.version = QLabel(f"Backend-Code: {VERSION}")
        self.service = QLabel("Dienst: wird geprüft …")
        self.api = QLabel("API: wird geprüft …")
        self.config = QLabel("Konfiguration: wird geprüft …")
        for widget in (self.version, self.service, self.api, self.config):
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(widget)
        self.refresh = QPushButton("Status aktualisieren")
        self.refresh.clicked.connect(self.refresh_requested)
        layout.addWidget(self.refresh)
        layout.addStretch(1)

    def set_status(self, service: dict, api: dict):
        active = service.get("ActiveState", "unbekannt")
        enabled = service.get("UnitFileState", "unbekannt")
        self.service.setText(f"Dienst: {active} · Systemstart: {enabled}")
        if api.get("ok"):
            self.api.setText(f"API: bereit · {api.get('url')}")
        else:
            self.api.setText(f"API: nicht bereit · {api.get('url', '')} · {api.get('error', '')}")
        try:
            snap = load_snapshot()
            self.config.setText(f"Konfiguration: {snap.path} · {len(snap.values)} Einträge")
        except Exception as exc:
            self.config.setText(f"Konfiguration: nicht lesbar · {exc}")


class SettingsPage(QWidget):
    saved = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.snapshot = None
        self.dirty: dict[str, str] = {}
        self.loading = False
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Einstellungen durchsuchen …")
        self.category = QComboBox(); self.category.addItem("Alle Kategorien")
        self.advanced = QCheckBox("Erweiterte Ansicht")
        top.addWidget(self.search, 1); top.addWidget(self.category); top.addWidget(self.advanced)
        layout.addLayout(top)
        self.notice = QLabel("")
        self.notice.setWordWrap(True); layout.addWidget(self.notice)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Einstellung", "Wert", "Kategorie", "Quelle", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.reload_btn = QPushButton("Neu laden")
        self.validate_btn = QPushButton("Validieren")
        self.save_btn = QPushButton("Speichern")
        self.apply_btn = QPushButton("Speichern + Dienst neu starten")
        buttons.addWidget(self.reload_btn); buttons.addWidget(self.validate_btn); buttons.addStretch(1); buttons.addWidget(self.save_btn); buttons.addWidget(self.apply_btn)
        layout.addLayout(buttons)
        self.reload_btn.clicked.connect(self.load)
        self.validate_btn.clicked.connect(self.validate)
        self.save_btn.clicked.connect(lambda: self.save(False))
        self.apply_btn.clicked.connect(lambda: self.save(True))
        self.search.textChanged.connect(self.filter_rows)
        self.category.currentTextChanged.connect(self.filter_rows)
        self.advanced.toggled.connect(self.filter_rows)
        self.table.itemChanged.connect(self._changed)
        self.load()

    def load(self):
        self.loading = True; self.dirty.clear()
        try:
            self.snapshot = load_snapshot()
            effective, origins = effective_environment()
            metas = settings_inventory()
            known_env = {alias for meta in metas for alias in meta.env_names}
            categories = sorted({m.category for m in metas})
            self.category.blockSignals(True); self.category.clear(); self.category.addItem("Alle Kategorien"); self.category.addItems(categories + ["Unbekannt"]); self.category.blockSignals(False)
            rows = []
            for meta in metas:
                env = meta.env_names[0] if meta.env_names else meta.name
                value = effective.get(env, "" if meta.default is None else str(meta.default))
                source = origins.get(env, "Default")
                rows.append((env, value, meta.category, source, "Secret" if meta.secret else "", meta.secret))
            for key, value in self.snapshot.values.items():
                if key not in known_env:
                    rows.append((key, "" if value is None else value, "Unbekannt", "file", "Nicht im Schema", key in SECRET_ENV_KEYS))
            self.table.setRowCount(len(rows))
            for row, (key, value, category, source, status, secret) in enumerate(rows):
                key_item = QTableWidgetItem(key); key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                value_item = QTableWidgetItem(MASK if secret and value else str(value))
                value_item.setData(Qt.ItemDataRole.UserRole, {"key": key, "secret": secret, "initial": str(value)})
                cat_item = QTableWidgetItem(category); cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                source_text = source + (" (überschreibt Datei)" if source == "environment" and key in self.snapshot.values else "")
                src_item = QTableWidgetItem(source_text); src_item.setFlags(src_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                stat_item = QTableWidgetItem(status); stat_item.setFlags(stat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                for col, item in enumerate((key_item, value_item, cat_item, src_item, stat_item)): self.table.setItem(row, col, item)
            self.notice.setText(f"Datei: {self.snapshot.path} · Änderungen sind erst nach Speichern und bei Serverwerten nach kontrolliertem Neustart aktiv.")
        except PermissionError:
            self.snapshot = None; self.table.setRowCount(0)
            self.notice.setText("Konfiguration ist geschützt. Für Änderungen wird die paketierte administrative Autorisierung benötigt.")
        except Exception as exc:
            self.snapshot = None; self.table.setRowCount(0); self.notice.setText(f"Konfiguration konnte nicht geladen werden: {exc}")
        finally:
            self.loading = False; self.filter_rows()

    def _changed(self, item: QTableWidgetItem):
        if self.loading or item.column() != 1: return
        meta = item.data(Qt.ItemDataRole.UserRole) or {}
        key = meta.get("key")
        if not key: return
        value = item.text()
        if meta.get("secret") and value == MASK:
            self.dirty.pop(key, None); return
        self.dirty[key] = value
        self.table.item(item.row(), 4).setText("Bearbeitet")
        self.notice.setText(f"{len(self.dirty)} Änderung(en) noch nicht gespeichert.")

    def filter_rows(self):
        query = self.search.text().strip().lower()
        category = self.category.currentText()
        advanced = self.advanced.isChecked()
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).text().lower()
            cat = self.table.item(row, 2).text()
            show = (not query or query in key or query in cat.lower()) and (category == "Alle Kategorien" or category == cat)
            if not advanced and cat in {"Erweitert", "Unbekannt"}: show = False
            self.table.setRowHidden(row, not show)

    def validate(self) -> bool:
        if self.snapshot is None:
            QMessageBox.warning(self, APP_NAME, "Keine lesbare Konfiguration geladen."); return False
        from app.settings_store import validate_supported_values
        candidate = dict(self.snapshot.values); candidate.update(self.dirty)
        try:
            # Do not let the launching shell silently replace candidate values during GUI validation.
            validate_supported_values(candidate, environ={})
        except Exception as exc:
            QMessageBox.critical(self, "Validierung fehlgeschlagen", str(exc)); return False
        QMessageBox.information(self, APP_NAME, "Konfiguration ist syntaktisch und gemäß Schema gültig.")
        return True

    def save(self, restart: bool):
        if not self.dirty:
            QMessageBox.information(self, APP_NAME, "Keine ungespeicherten Änderungen."); return
        if self.snapshot is None or not self.validate(): return
        try:
            self.snapshot = save_updates(self.dirty, path=self.snapshot.path, expected_digest=self.snapshot.digest, environ={})
        except PermissionError:
            self._save_privileged(restart); return
        except ConfigConflict as exc:
            QMessageBox.warning(self, "Konflikt", f"Die Datei wurde parallel geändert. Neu laden und Änderungen prüfen.\n\n{exc}"); return
        except Exception as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc)); return
        self.dirty.clear(); get_settings.cache_clear(); self.saved.emit(); self.load()
        if restart: self._service_restart()

    def _save_privileged(self, restart: bool):
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.critical(self, APP_NAME, "Geschützte Konfiguration kann ohne installierten Admin-Helper und pkexec nicht geschrieben werden."); return
        payload = json.dumps({"expected_digest": self.snapshot.digest, "updates": self.dirty}, ensure_ascii=False).encode()
        proc = subprocess.run(["pkexec", str(ADMIN_HELPER), "config-update"], input=payload, capture_output=True, timeout=60)
        if proc.returncode:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", proc.stderr.decode(errors="replace")); return
        self.dirty.clear(); get_settings.cache_clear(); self.saved.emit(); self.load()
        if restart: self._service_restart()

    def _service_restart(self):
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.warning(self, APP_NAME, "Gespeichert, aber kein Admin-Helper für den Neustart verfügbar."); return
        subprocess.Popen(["pkexec", str(ADMIN_HELPER), "service-restart"])


class ServicesPage(QWidget):
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__(); layout = QVBoxLayout(self)
        self.status = QLabel("Dienststatus wird geladen …"); layout.addWidget(self.status)
        row = QHBoxLayout()
        for text, action in (("Start", "service-start"), ("Stopp", "service-stop"), ("Neustart", "service-restart")):
            btn = QPushButton(text); btn.clicked.connect(lambda _=False, a=action: self.admin_action(a)); row.addWidget(btn)
        layout.addLayout(row)
        self.boot = QCheckBox("TriForce Server beim Hochfahren starten")
        self.desktop = QCheckBox("Control Center bei Desktop-Anmeldung öffnen")
        self.boot.clicked.connect(self.toggle_boot); self.desktop.clicked.connect(self.toggle_desktop)
        layout.addWidget(self.boot); layout.addWidget(self.desktop)
        note = QLabel("Dienstzustand und Systemstart sind getrennt: Start/Stopp ändern den aktuellen Zustand, der Systemstart-Schalter nur zukünftige Boots.")
        note.setWordWrap(True); layout.addWidget(note); layout.addStretch(1)
        self.update_status()

    def update_status(self):
        info = service_info(); self.status.setText(f"triforce.service: {info.get('ActiveState','unbekannt')} / {info.get('SubState','')} · Autostart: {info.get('UnitFileState','unbekannt')}")
        self.boot.blockSignals(True); self.boot.setChecked(info.get("UnitFileState") == "enabled"); self.boot.blockSignals(False)
        self.desktop.blockSignals(True); self.desktop.setChecked(AUTOSTART_FILE.exists()); self.desktop.blockSignals(False)

    def admin_action(self, action: str):
        if not self._admin(action): return
        QTimer.singleShot(800, self.update_status)

    def _admin(self, action: str) -> bool:
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.warning(self, APP_NAME, "Paketierter Admin-Helper/pkexec ist nicht verfügbar."); return False
        try: cp = subprocess.run(["pkexec", str(ADMIN_HELPER), action], capture_output=True, timeout=90)
        except Exception as exc: QMessageBox.critical(self, APP_NAME, str(exc)); return False
        if cp.returncode:
            QMessageBox.critical(self, APP_NAME, cp.stderr.decode(errors="replace") or f"Aktion fehlgeschlagen ({cp.returncode})"); return False
        self.refresh_requested.emit(); return True

    def toggle_boot(self, checked: bool):
        if not self._admin("service-enable" if checked else "service-disable"):
            self.boot.setChecked(not checked)
        self.update_status()

    def toggle_desktop(self, checked: bool):
        try:
            if checked:
                AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
                if DESKTOP_FILE.is_file(): shutil.copyfile(DESKTOP_FILE, AUTOSTART_FILE)
                else:
                    AUTOSTART_FILE.write_text("[Desktop Entry]\nType=Application\nName=TriForce Control Center\nExec=triforce-control-center\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
            else:
                AUTOSTART_FILE.unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc)); self.desktop.setChecked(not checked)


class SetupPage(QWidget):
    def __init__(self):
        super().__init__(); self.active = None
        layout = QVBoxLayout(self)
        self.tasks = QTableWidget(len(TASKS), 4); self.tasks.setHorizontalHeaderLabels(["Aufgabe", "Typ", "Status", "Aktion"])
        self.tasks.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tasks, 1)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setMaximumHeight(180); layout.addWidget(self.detail)
        for row, task in enumerate(TASKS.values()):
            self.tasks.setItem(row, 0, QTableWidgetItem(task.title))
            self.tasks.setItem(row, 1, QTableWidgetItem("Admin" if task.requires_admin else "Prüfung"))
            self.tasks.setItem(row, 2, QTableWidgetItem("nicht geprüft"))
            button = QPushButton("Prüfen" if not task.mutating else "Plan / Ausführen")
            button.clicked.connect(lambda _=False, tid=task.task_id, r=row: self.task_action(tid, r))
            self.tasks.setCellWidget(row, 3, button)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.hide(); layout.addWidget(self.progress)

    def task_action(self, task_id: str, row: int):
        task = TASKS[task_id]
        self.detail.setPlainText(json.dumps(task.plan(), ensure_ascii=False, indent=2))
        if not task.mutating:
            result = run_check(task_id); self.tasks.item(row, 2).setText(result.status); self.detail.appendPlainText("\nErgebnis:\n" + json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)); return
        answer = QMessageBox.question(self, task.title, "Geplante Änderungen:\n\n- " + "\n- ".join(task.changes) + f"\n\nWiederholung: {task.repeat_behavior}\n\nJetzt autorisieren und ausführen?")
        if answer != QMessageBox.StandardButton.Yes: return
        if self.active is not None:
            QMessageBox.warning(self, APP_NAME, "Es läuft bereits ein verändernder Setup-Auftrag."); return
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.critical(self, APP_NAME, "Admin-Helper/pkexec fehlt."); return
        from PyQt6.QtCore import QProcess
        proc = QProcess(self); self.active = proc; self.progress.show(); self.tasks.item(row, 2).setText("läuft …")
        proc.setProgram("pkexec"); proc.setArguments([str(ADMIN_HELPER), task.helper_action])
        proc.readyReadStandardOutput.connect(lambda: self.detail.appendPlainText(bytes(proc.readAllStandardOutput()).decode(errors="replace")))
        proc.readyReadStandardError.connect(lambda: self.detail.appendPlainText(bytes(proc.readAllStandardError()).decode(errors="replace")))
        def done(code, _status):
            self.progress.hide(); self.active = None
            result = run_check(task_id); self.tasks.item(row, 2).setText(result.status if code == 0 else f"Fehler {code}")
            self.detail.appendPlainText("\nVerifikation:\n" + json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
        proc.finished.connect(done); proc.start()


class LogsPage(QWidget):
    def __init__(self):
        super().__init__(); layout = QVBoxLayout(self)
        row = QHBoxLayout(); self.refresh = QPushButton("Logs aktualisieren"); self.diag = QPushButton("Diagnose ausführen"); self.export = QPushButton("Diagnosebericht speichern")
        row.addWidget(self.refresh); row.addWidget(self.diag); row.addWidget(self.export); row.addStretch(1); layout.addLayout(row)
        self.output = QPlainTextEdit(); self.output.setReadOnly(True); layout.addWidget(self.output, 1)
        self.refresh.clicked.connect(self.load_logs); self.diag.clicked.connect(self.run_diag); self.export.clicked.connect(self.export_diag)
        self.load_logs()

    def load_logs(self): self.output.setPlainText(recent_logs())

    def run_diag(self):
        result = run_check("diagnose"); self.output.setPlainText(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))

    def export_diag(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Diagnosebericht speichern", "triforce-diagnose.json", "JSON (*.json)")
        if not filename: return
        result = run_check("diagnose").to_dict()
        report = {"version": VERSION, "diagnose": result, "service": service_info(), "api": api_health(), "logs": recent_logs(100)}
        # Intentionally no raw config/.env values in reports.
        Path(filename).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        QMessageBox.information(self, APP_NAME, "Redigierter Diagnosebericht gespeichert.")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(APP_NAME); self.resize(1180, 760)
        self.pool = QThreadPool.globalInstance()
        splitter = QSplitter(); self.nav = QListWidget(); self.nav.setMaximumWidth(240)
        self.stack = QStackedWidget(); splitter.addWidget(self.nav); splitter.addWidget(self.stack); splitter.setStretchFactor(1, 1); self.setCentralWidget(splitter)
        self.overview = OverviewPage(); self.settings = SettingsPage(); self.services = ServicesPage(); self.setup = SetupPage(); self.logs = LogsPage()
        for title, page in (("Übersicht", self.overview), ("Einstellungen", self.settings), ("Dienste", self.services), ("Einrichtung", self.setup), ("Logs & Diagnose", self.logs)):
            self.nav.addItem(title); self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex); self.nav.setCurrentRow(0)
        self.overview.refresh_requested.connect(self.refresh_status); self.services.refresh_requested.connect(self.refresh_status); self.settings.saved.connect(self.refresh_status)
        action = QAction("Aktualisieren", self); action.setShortcut("Ctrl+R"); action.triggered.connect(self.refresh_status); self.addAction(action)
        self.refresh_status()
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_status); self.timer.start(15_000)

    def refresh_status(self):
        worker = Worker(lambda: (service_info(), api_health()))
        worker.signals.done.connect(lambda data: self._status_ready(*data)); worker.signals.error.connect(lambda err: self.statusBar().showMessage(err, 5000)); self.pool.start(worker)

    def _status_ready(self, service, api):
        self.overview.set_status(service, api); self.services.update_status()
        self.statusBar().showMessage("Status aktualisiert", 2500)


def main() -> int:
    smoke = "--smoke-test" in sys.argv
    screenshot = None
    for arg in tuple(sys.argv[1:]):
        if arg.startswith("--smoke-screenshot="):
            smoke = True
            screenshot = arg.split("=", 1)[1]
    qt_argv = [arg for arg in sys.argv if arg != "--smoke-test" and not arg.startswith("--smoke-screenshot=")]
    app = QApplication(qt_argv); app.setApplicationName(APP_NAME); app.setOrganizationName("AILinux")
    window = MainWindow(); window.show()
    if smoke:
        def finish_smoke():
            if screenshot:
                window.grab().save(screenshot)
            print(f"GUI_SMOKE_OK pages={window.stack.count()} settings_rows={window.settings.table.rowCount()}", flush=True)
            app.quit()
        QTimer.singleShot(1200, finish_smoke)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
