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

from PyQt6.QtCore import QObject, QProcess, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.config import VERSION, get_settings
from app.settings_store import (
    MASKED_SECRET_VALUE, SECRET_ENV_KEYS, ConfigConflict, ConfigSnapshot,
    effective_environment, load_snapshot, redact, redact_dotenv_text,
    restore_masked_secrets, save_raw_text, save_updates, settings_inventory,
    validate_supported_values,
)
from app.setup_tasks import TASKS, run_check
from app.setup_job import job_is_running, read_setup_job

APP_NAME = "TriForce Control Center"
ADMIN_HELPER = Path("/usr/lib/triforce/triforce-admin-helper")
DESKTOP_FILE = Path("/usr/share/applications/triforce-control-center.desktop")
AUTOSTART_FILE = Path.home() / ".config/autostart/triforce-control-center.desktop"
MASK = MASKED_SECRET_VALUE


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
        self.snapshot: ConfigSnapshot | None = None
        self.dirty: dict[str, str] = {}
        self.admin_proc: QProcess | None = None
        self.loading = False
        self.protected = False
        self.raw_original = ""

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Einstellungen durchsuchen …")
        self.category = QComboBox(); self.category.addItem("Alle Kategorien")
        self.advanced = QCheckBox("Erweiterte Ansicht")
        top.addWidget(self.search, 1); top.addWidget(self.category); top.addWidget(self.advanced)
        layout.addLayout(top)
        self.notice = QLabel(""); self.notice.setWordWrap(True); layout.addWidget(self.notice)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Einstellung", "Wert", "Typ", "Kategorie", "Quelle", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in range(2, 6): self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True); layout.addWidget(self.table, 1)

        self.raw_editor = QPlainTextEdit()
        self.raw_editor.setPlaceholderText("Redigierte .env-Rohkonfiguration")
        self.raw_editor.hide(); layout.addWidget(self.raw_editor, 1)
        self.raw_note = QLabel("Rohmodus: bekannte Secrets werden nur maskiert angezeigt. Unveränderte Masken werden beim Speichern mit den geschützten Originalwerten zusammengeführt.")
        self.raw_note.setWordWrap(True); self.raw_note.hide(); layout.addWidget(self.raw_note)

        buttons = QHBoxLayout()
        self.reload_btn = QPushButton("Neu laden")
        self.unlock_btn = QPushButton("Geschützte Konfiguration laden")
        self.unlock_btn.hide()
        self.raw_toggle = QPushButton("Rohkonfiguration")
        self.validate_btn = QPushButton("Validieren")
        self.save_btn = QPushButton("Speichern")
        self.raw_save_btn = QPushButton("Rohtext speichern"); self.raw_save_btn.hide()
        self.apply_btn = QPushButton("Speichern + Dienst neu starten")
        for button in (self.reload_btn, self.unlock_btn, self.raw_toggle, self.validate_btn): buttons.addWidget(button)
        buttons.addStretch(1); buttons.addWidget(self.save_btn); buttons.addWidget(self.raw_save_btn); buttons.addWidget(self.apply_btn)
        layout.addLayout(buttons)

        self.reload_btn.clicked.connect(self.reload_current)
        self.unlock_btn.clicked.connect(self.load_privileged)
        self.raw_toggle.clicked.connect(self.toggle_raw)
        self.validate_btn.clicked.connect(self.validate)
        self.save_btn.clicked.connect(lambda: self.save(False))
        self.raw_save_btn.clicked.connect(lambda: self.save_raw(False))
        self.apply_btn.clicked.connect(lambda: self.save(False, restart=True) if not self.raw_editor.isVisible() else self.save_raw(True))
        self.search.textChanged.connect(self.filter_rows)
        self.category.currentTextChanged.connect(self.filter_rows)
        self.advanced.toggled.connect(self.filter_rows)
        self.table.itemChanged.connect(self._changed)
        self.load()

    def reload_current(self):
        if self.protected: self.load_privileged()
        else: self.load()

    def _effective_from_snapshot(self, snapshot: ConfigSnapshot) -> tuple[dict[str, str], dict[str, str]]:
        effective: dict[str, str] = {}
        origins: dict[str, str] = {}
        for meta in settings_inventory():
            env = meta.env_names[0] if meta.env_names else meta.name
            if env in snapshot.values:
                value = snapshot.values[env]
                if value is not None: effective[env] = value
                origins[env] = "file"
            elif meta.default is not None:
                effective[env] = str(meta.default)
                origins[env] = "Default"
        for key, value in snapshot.values.items():
            if value is not None and key not in effective:
                effective[key] = value; origins[key] = "file"
        return effective, origins

    def _populate(self, snapshot: ConfigSnapshot, effective: dict[str, str], origins: dict[str, str], raw_text: str):
        self.loading = True; self.snapshot = snapshot; self.dirty.clear()
        metas = settings_inventory(); known_env = {alias for meta in metas for alias in meta.env_names}
        categories = sorted({m.category for m in metas})
        self.category.blockSignals(True); self.category.clear(); self.category.addItem("Alle Kategorien"); self.category.addItems(categories + ["Unbekannt"]); self.category.blockSignals(False)
        rows = []
        for meta in metas:
            env = meta.env_names[0] if meta.env_names else meta.name
            value = effective.get(env, "" if meta.default is None else str(meta.default)); source = origins.get(env, "Default")
            limits = f"Grenzen: {meta.minimum if meta.minimum is not None else '–'} … {meta.maximum if meta.maximum is not None else '–'}" if meta.minimum is not None or meta.maximum is not None else ""
            choices = f"Auswahl: {', '.join(meta.choices)}" if meta.choices else ""
            tooltip = "\n".join(x for x in (meta.description, limits, choices, f"Speicher: {meta.storage}", "Neustart erforderlich" if meta.required_restart else "Live übernehmbar") if x)
            rows.append((env, value, meta.value_type, meta.category, source, "Secret" if meta.secret else "", meta.secret, tooltip))
        for key, value in snapshot.values.items():
            if key not in known_env:
                rows.append((key, "" if value is None else value, "unbekannt", "Unbekannt", "file", "Nicht im Schema", key in SECRET_ENV_KEYS, "Kein bekannter Verbraucher; nur eingeschränkt validiert.") )
        self.table.setRowCount(len(rows))
        for row, (key, value, value_type, category, source, status, secret, tooltip) in enumerate(rows):
            key_item = QTableWidgetItem(key); key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            shown = MASK if secret and value not in (None, "") else str(value)
            value_item = QTableWidgetItem(shown); value_item.setData(Qt.ItemDataRole.UserRole, {"key": key, "secret": secret, "initial": str(value)})
            type_item = QTableWidgetItem(value_type); type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            cat_item = QTableWidgetItem(category); cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            src_item = QTableWidgetItem(source); src_item.setFlags(src_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            stat_item = QTableWidgetItem(status); stat_item.setFlags(stat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            for col, item in enumerate((key_item, value_item, type_item, cat_item, src_item, stat_item)):
                item.setToolTip(tooltip); self.table.setItem(row, col, item)
        self.raw_editor.setPlainText(raw_text)
        self.notice.setText(f"Datei: {snapshot.path} · Änderungen sind gespeichert erst nach kontrolliertem Neustart aktiv, sofern das Feld keinen getesteten Live-Reload besitzt.")
        self.unlock_btn.hide(); self.loading = False; self.filter_rows()

    def load(self):
        try:
            snapshot = load_snapshot(); effective, origins = effective_environment()
            raw = snapshot.path.read_text(encoding="utf-8") if snapshot.path.exists() else ""
            self.protected = False; self.raw_original = raw
            self._populate(snapshot, effective, origins, redact_dotenv_text(raw))
        except PermissionError:
            self.snapshot = None; self.table.setRowCount(0); self.raw_editor.clear(); self.protected = True
            self.notice.setText("Systemkonfiguration ist geschützt. Sie kann redigiert über PolicyKit geladen werden; Secretwerte werden dabei niemals ausgegeben.")
            self.unlock_btn.show()
        except Exception as exc:
            self.snapshot = None; self.table.setRowCount(0); self.notice.setText(f"Konfiguration konnte nicht geladen werden: {exc}")

    def load_privileged(self):
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.critical(self, APP_NAME, "Admin-Helper/pkexec ist nicht verfügbar."); return
        if self.admin_proc is not None: return
        proc = QProcess(self); self.admin_proc = proc; proc.setProgram("pkexec"); proc.setArguments([str(ADMIN_HELPER), "config-read"])
        def finished(code, _status):
            out = bytes(proc.readAllStandardOutput()).decode(errors="replace"); err = bytes(proc.readAllStandardError()).decode(errors="replace"); self.admin_proc = None
            if code != 0:
                QMessageBox.critical(self, APP_NAME, err or f"Konfiguration konnte nicht geladen werden ({code})"); return
            try:
                data = json.loads(out); values = data["values"]
                snapshot = ConfigSnapshot(Path(data["path"]), values, data["digest"], None)
                effective, origins = self._effective_from_snapshot(snapshot)
                self.protected = True; self.raw_original = ""
                self._populate(snapshot, effective, origins, data["raw"])
            except Exception as exc:
                QMessageBox.critical(self, APP_NAME, f"Ungültige Antwort des Admin-Helpers: {exc}")
        proc.finished.connect(finished); proc.start()

    def toggle_raw(self):
        show = not self.raw_editor.isVisible()
        if show and self.snapshot is None:
            QMessageBox.information(self, APP_NAME, "Lade zuerst die Konfiguration."); return
        self.raw_editor.setVisible(show); self.raw_note.setVisible(show); self.raw_save_btn.setVisible(show)
        self.table.setVisible(not show); self.save_btn.setVisible(not show); self.validate_btn.setVisible(not show)
        self.search.setEnabled(not show); self.category.setEnabled(not show)
        self.raw_toggle.setText("Strukturierte Ansicht" if show else "Rohkonfiguration")

    def _changed(self, item: QTableWidgetItem):
        if self.loading or item.column() != 1: return
        meta = item.data(Qt.ItemDataRole.UserRole) or {}; key = meta.get("key")
        if not key: return
        value = item.text()
        if meta.get("secret") and value == MASK: self.dirty.pop(key, None); return
        self.dirty[key] = value; self.table.item(item.row(), 5).setText("Bearbeitet")
        self.notice.setText(f"{len(self.dirty)} Änderung(en) noch nicht gespeichert.")

    def filter_rows(self):
        query = self.search.text().strip().lower(); category = self.category.currentText(); advanced = self.advanced.isChecked()
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).text().lower(); cat = self.table.item(row, 3).text()
            show = (not query or query in key or query in cat.lower()) and (category == "Alle Kategorien" or category == cat)
            if not advanced and cat in {"Erweitert", "Unbekannt"}: show = False
            self.table.setRowHidden(row, not show)

    def validate(self) -> bool:
        if self.snapshot is None: QMessageBox.warning(self, APP_NAME, "Keine Konfiguration geladen."); return False
        candidate = {k: v for k, v in self.snapshot.values.items() if not (k in SECRET_ENV_KEYS and v == MASK)}; candidate.update(self.dirty)
        try: validate_supported_values(candidate, environ={})
        except Exception as exc: QMessageBox.critical(self, "Validierung fehlgeschlagen", str(exc)); return False
        QMessageBox.information(self, APP_NAME, "Konfiguration ist gemäß gemeinsamem Schema gültig."); return True

    def save(self, restart: bool = False):
        if not self.dirty: QMessageBox.information(self, APP_NAME, "Keine ungespeicherten Änderungen."); return
        if self.snapshot is None or not self.validate(): return
        if self.protected: self._save_privileged(restart); return
        try: self.snapshot = save_updates(self.dirty, path=self.snapshot.path, expected_digest=self.snapshot.digest, environ={})
        except PermissionError: self.protected = True; self._save_privileged(restart); return
        except ConfigConflict as exc: QMessageBox.warning(self, "Konflikt", f"Datei wurde parallel geändert.\n\n{exc}"); return
        except Exception as exc: QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc)); return
        self.dirty.clear(); get_settings.cache_clear(); self.saved.emit(); self.load()
        if restart: self._service_restart()

    def _run_admin_with_stdin(self, action: str, payload: bytes, on_success):
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.critical(self, APP_NAME, "Admin-Helper/pkexec ist nicht verfügbar."); return
        if self.admin_proc is not None: QMessageBox.warning(self, APP_NAME, "Eine administrative Aktion läuft bereits."); return
        proc = QProcess(self); self.admin_proc = proc; proc.setProgram("pkexec"); proc.setArguments([str(ADMIN_HELPER), action])
        proc.started.connect(lambda: (proc.write(payload), proc.closeWriteChannel()))
        def finished(code, _status):
            err = bytes(proc.readAllStandardError()).decode(errors="replace"); self.admin_proc = None
            if code != 0: QMessageBox.critical(self, APP_NAME, err or f"Admin-Helper beendet mit {code}"); return
            on_success()
        proc.finished.connect(finished); proc.start()

    def _save_privileged(self, restart: bool):
        payload = json.dumps({"expected_digest": self.snapshot.digest, "updates": self.dirty}, ensure_ascii=False).encode()
        def success():
            self.dirty.clear(); get_settings.cache_clear(); self.saved.emit(); self.load_privileged()
            if restart: QTimer.singleShot(400, self._service_restart)
        self._run_admin_with_stdin("config-update", payload, success)

    def save_raw(self, restart: bool):
        if self.snapshot is None: return
        text = self.raw_editor.toPlainText()
        if self.protected:
            payload = json.dumps({"expected_digest": self.snapshot.digest, "text": text}, ensure_ascii=False).encode()
            def success():
                get_settings.cache_clear(); self.saved.emit(); self.load_privileged()
                if restart: QTimer.singleShot(400, self._service_restart)
            self._run_admin_with_stdin("config-raw-update", payload, success); return
        try:
            restored = restore_masked_secrets(text, self.raw_original)
            save_raw_text(restored, path=self.snapshot.path, expected_digest=self.snapshot.digest, environ={})
        except ConfigConflict as exc: QMessageBox.warning(self, "Konflikt", str(exc)); return
        except Exception as exc: QMessageBox.critical(self, "Rohkonfiguration ungültig", str(exc)); return
        get_settings.cache_clear(); self.saved.emit(); self.load()
        if restart: self._service_restart()

    def _service_restart(self):
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.warning(self, APP_NAME, "Gespeichert, aber kein Admin-Helper für den Neustart verfügbar."); return
        if self.admin_proc is not None: QTimer.singleShot(250, self._service_restart); return
        proc = QProcess(self); self.admin_proc = proc; proc.setProgram("pkexec"); proc.setArguments([str(ADMIN_HELPER), "service-restart"])
        def finished(code, _status):
            err = bytes(proc.readAllStandardError()).decode(errors="replace"); self.admin_proc = None
            if code != 0: QMessageBox.warning(self, APP_NAME, err or f"Neustart fehlgeschlagen ({code})")
        proc.finished.connect(finished); proc.start()


class ServicesPage(QWidget):
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__(); self.admin_proc: QProcess | None = None; self.pool = QThreadPool.globalInstance(); layout = QVBoxLayout(self)
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

    def set_status(self, info: dict):
        self.status.setText(f"triforce.service: {info.get('ActiveState','unbekannt')} / {info.get('SubState','')} · Autostart: {info.get('UnitFileState','unbekannt')}")
        self.boot.blockSignals(True); self.boot.setChecked(info.get("UnitFileState") == "enabled"); self.boot.blockSignals(False)
        self.desktop.blockSignals(True); self.desktop.setChecked(AUTOSTART_FILE.exists()); self.desktop.blockSignals(False)

    def update_status(self):
        worker = Worker(service_info)
        worker.signals.done.connect(self.set_status)
        worker.signals.error.connect(lambda err: self.status.setText(f"Dienststatus fehlgeschlagen: {err}"))
        self.pool.start(worker)

    def admin_action(self, action: str):
        self._admin_async(action)

    def _admin_async(self, action: str, *, revert_boot: bool = False):
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.warning(self, APP_NAME, "Paketierter Admin-Helper/pkexec ist nicht verfügbar.")
            if revert_boot: self.update_status()
            return
        if self.admin_proc is not None:
            QMessageBox.warning(self, APP_NAME, "Eine Dienstaktion läuft bereits.")
            if revert_boot: self.update_status()
            return
        proc = QProcess(self); self.admin_proc = proc
        proc.setProgram("pkexec"); proc.setArguments([str(ADMIN_HELPER), action])
        self.status.setText(f"triforce.service: Aktion {action} läuft …")
        def finished(code, _status):
            err = bytes(proc.readAllStandardError()).decode(errors="replace")
            self.admin_proc = None
            if code != 0: QMessageBox.critical(self, APP_NAME, err or f"Aktion fehlgeschlagen ({code})")
            self.update_status(); self.refresh_requested.emit()
        proc.finished.connect(finished); proc.start()

    def toggle_boot(self, checked: bool):
        self._admin_async("service-enable" if checked else "service-disable", revert_boot=True)

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
        super().__init__(); self.launch_proc: QProcess | None = None
        layout = QVBoxLayout(self)
        self.tasks = QTableWidget(len(TASKS), 4); self.tasks.setHorizontalHeaderLabels(["Aufgabe", "Typ", "Status", "Aktion"])
        self.tasks.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tasks, 1)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setMaximumHeight(200); layout.addWidget(self.detail)
        self.rows: dict[str, int] = {}
        for row, task in enumerate(TASKS.values()):
            self.rows[task.task_id] = row
            self.tasks.setItem(row, 0, QTableWidgetItem(task.title))
            self.tasks.setItem(row, 1, QTableWidgetItem("Admin" if task.requires_admin else "Prüfung"))
            self.tasks.setItem(row, 2, QTableWidgetItem("nicht geprüft"))
            button = QPushButton("Prüfen" if not task.mutating else "Plan / Ausführen")
            button.clicked.connect(lambda _=False, tid=task.task_id, r=row: self.task_action(tid, r))
            self.tasks.setCellWidget(row, 3, button)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.hide(); layout.addWidget(self.progress)
        self.cancel_button = QPushButton("Laufenden Setup-Auftrag abbrechen"); self.cancel_button.hide(); self.cancel_button.clicked.connect(self.cancel_job); layout.addWidget(self.cancel_button)
        self.job_timer = QTimer(self); self.job_timer.timeout.connect(self.refresh_job_status); self.job_timer.start(1000)
        self.refresh_job_status()

    def task_action(self, task_id: str, row: int):
        task = TASKS[task_id]
        self.detail.setPlainText(json.dumps(task.plan(), ensure_ascii=False, indent=2))
        if not task.mutating:
            self.tasks.item(row, 2).setText("prüft …")
            worker = Worker(run_check, task_id)
            def checked(result):
                self.tasks.item(row, 2).setText(result.status)
                self.detail.appendPlainText("\nErgebnis:\n" + json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
            worker.signals.done.connect(checked)
            worker.signals.error.connect(lambda err: self.tasks.item(row, 2).setText(f"Fehler: {err}"))
            QThreadPool.globalInstance().start(worker); return
        if job_is_running():
            QMessageBox.warning(self, APP_NAME, "Es läuft bereits ein verändernder Setup-Auftrag."); self.refresh_job_status(); return
        answer = QMessageBox.question(self, task.title, "Geplante Änderungen:\n\n- " + "\n- ".join(task.changes) + f"\n\nWiederholung: {task.repeat_behavior}\n\nJetzt autorisieren und als überwachten Systemauftrag starten?")
        if answer != QMessageBox.StandardButton.Yes: return
        if self.launch_proc is not None:
            QMessageBox.warning(self, APP_NAME, "Die Autorisierung eines Setup-Auftrags läuft bereits."); return
        if not ADMIN_HELPER.is_file() or not shutil.which("pkexec"):
            QMessageBox.critical(self, APP_NAME, "Admin-Helper/pkexec fehlt."); return
        proc = QProcess(self); self.launch_proc = proc; self.progress.show(); self.tasks.item(row, 2).setText("wird gestartet …")
        proc.setProgram("pkexec"); proc.setArguments([str(ADMIN_HELPER), "setup-start", task_id])
        def done(code, _status):
            err = bytes(proc.readAllStandardError()).decode(errors="replace")
            self.launch_proc = None
            if code != 0:
                self.progress.hide(); self.tasks.item(row, 2).setText(f"Startfehler {code}")
                QMessageBox.critical(self, APP_NAME, err or f"Setup-Auftrag konnte nicht gestartet werden ({code})")
            QTimer.singleShot(250, self.refresh_job_status)
        proc.finished.connect(done); proc.start()

    def cancel_job(self):
        if self.launch_proc is not None:
            QMessageBox.warning(self, APP_NAME, "Eine Setup-Autorisierung läuft bereits."); return
        status = read_setup_job()
        if not job_is_running(status):
            self.refresh_job_status(); return
        if QMessageBox.question(self, APP_NAME, "Laufenden Setup-Auftrag wirklich abbrechen?") != QMessageBox.StandardButton.Yes:
            return
        proc = QProcess(self); self.launch_proc = proc
        proc.setProgram("pkexec"); proc.setArguments([str(ADMIN_HELPER), "setup-cancel"])
        def done(code, _status):
            err = bytes(proc.readAllStandardError()).decode(errors="replace")
            self.launch_proc = None
            if code != 0:
                QMessageBox.critical(self, APP_NAME, err or f"Abbruch fehlgeschlagen ({code})")
            QTimer.singleShot(250, self.refresh_job_status)
        proc.finished.connect(done); proc.start()

    def refresh_job_status(self):
        status = read_setup_job()
        if not status:
            if self.launch_proc is None: self.progress.hide()
            return
        task_id = str(status.get("task_id") or "")
        state = str(status.get("state") or "unbekannt")
        row = self.rows.get(task_id)
        if row is not None:
            labels = {"queued": "wartet …", "running": "läuft …", "completed": "erfolgreich", "failed": "fehlgeschlagen", "rejected": "abgelehnt", "cancelled": "abgebrochen"}
            self.tasks.item(row, 2).setText(labels.get(state, state))
        self.progress.setVisible(state in {"queued", "running"} or self.launch_proc is not None)
        self.cancel_button.setVisible(state in {"queued", "running"})
        self.detail.setPlainText("Überwachter Setup-Auftrag:\n" + json.dumps(status, ensure_ascii=False, indent=2, default=str))


class LogsPage(QWidget):
    def __init__(self):
        super().__init__(); self.pool = QThreadPool.globalInstance(); layout = QVBoxLayout(self)
        row = QHBoxLayout(); self.refresh = QPushButton("Logs aktualisieren"); self.diag = QPushButton("Diagnose ausführen"); self.export = QPushButton("Diagnosebericht speichern")
        row.addWidget(self.refresh); row.addWidget(self.diag); row.addWidget(self.export); row.addStretch(1); layout.addLayout(row)
        self.output = QPlainTextEdit(); self.output.setReadOnly(True); layout.addWidget(self.output, 1)
        self.refresh.clicked.connect(self.load_logs); self.diag.clicked.connect(self.run_diag); self.export.clicked.connect(self.export_diag)
        self.load_logs()

    def _start(self, fn, done):
        worker = Worker(fn); worker.signals.done.connect(done); worker.signals.error.connect(lambda err: self.output.setPlainText(f"Fehler: {err}")); self.pool.start(worker)

    def load_logs(self):
        self.output.setPlainText("Logs werden geladen …")
        self._start(recent_logs, self.output.setPlainText)

    def run_diag(self):
        self.output.setPlainText("Diagnose läuft …")
        self._start(lambda: run_check("diagnose").to_dict(), lambda result: self.output.setPlainText(json.dumps(result, ensure_ascii=False, indent=2, default=str)))

    def export_diag(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Diagnosebericht speichern", "triforce-diagnose.json", "JSON (*.json)")
        if not filename: return
        self.output.setPlainText("Diagnosebericht wird erstellt …")
        def build_and_write():
            report = {"version": VERSION, "diagnose": run_check("diagnose").to_dict(), "service": service_info(), "api": api_health(), "logs": recent_logs(100)}
            Path(filename).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return filename
        self._start(build_and_write, lambda path: self.output.setPlainText(f"Redigierter Diagnosebericht gespeichert: {path}"))


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
        self.overview.set_status(service, api); self.services.set_status(service)
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
