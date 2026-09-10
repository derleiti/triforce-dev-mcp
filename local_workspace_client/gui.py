#!/usr/bin/env python3
"""PyQt6 GUI for the standalone TriForce Local Workspace Client."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)

from .runtime import WorkspaceRuntime


class WorkspaceWindow(QMainWindow):
    def __init__(self, pair_code: str = "") -> None:
        super().__init__()
        self.setWindowTitle("TriForce Local Workspace")
        self.resize(820, 650)
        self.proc: QProcess | None = None

        page = QWidget(self)
        self.setCentralWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        title = QLabel("TriForce Local Workspace")
        title.setFont(QFont(title.font().family(), 20, 600))
        layout.addWidget(title)
        subtitle = QLabel(
            "Pair one local folder with the current ChatGPT, Codex or Mistral TriForce MCP session. "
            "No TriForce account or API key is required."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addWidget(QLabel("Pairing code from TriForce"))
        self.pair = QLineEdit(pair_code.strip().upper())
        self.pair.setPlaceholderText("ABCD-1234-EF56-789A-BCDE-F012")
        layout.addWidget(self.pair)

        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Choose a local folder")
        choose = QPushButton("Choose folder…")
        choose.clicked.connect(self.choose_folder)
        row.addWidget(self.folder, 1)
        row.addWidget(choose)
        layout.addLayout(row)

        self.task = QPlainTextEdit()
        self.task.setPlaceholderText("Task for the AI, e.g. Analyze this project, find bugs and fix the tests")
        self.task.setMaximumHeight(100)
        layout.addWidget(self.task)

        access = QHBoxLayout()
        access.addWidget(QLabel("Access:"))
        self.read_only = QRadioButton("Read only")
        self.write = QRadioButton("Write")
        self.read_only.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.read_only)
        group.addButton(self.write)
        access.addWidget(self.read_only)
        access.addWidget(self.write)
        access.addStretch(1)
        layout.addLayout(access)

        buttons = QHBoxLayout()
        self.analyze_btn = QPushButton("Analyze folder")
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.copy_btn = QPushButton("Copy MCP URL")
        self.analyze_btn.clicked.connect(self.analyze)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.copy_btn.clicked.connect(self.copy_url)
        for button in (self.analyze_btn, self.start_btn, self.stop_btn, self.copy_btn):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.status = QLabel("Not paired")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)

        self.url = QLineEdit("https://api.ailinux.me/v1/mcp")
        self.url.setReadOnly(True)
        layout.addWidget(self.url)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Folder analysis and connection status")
        layout.addWidget(self.details, 1)

    def choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose workspace folder", self.folder.text() or str(Path.home()))
        if chosen:
            self.folder.setText(chosen)
            self.analyze()

    def runtime(self) -> WorkspaceRuntime:
        raw = self.folder.text().strip()
        if not raw:
            raise ValueError("Choose a workspace folder first")
        return WorkspaceRuntime(Path(raw), writable=self.write.isChecked(), task=self.task.toPlainText().strip())

    def analyze(self) -> None:
        try:
            info = self.runtime().analyze()
        except Exception as exc:
            QMessageBox.warning(self, "Workspace", str(exc))
            return
        self.details.setPlainText(json.dumps(info, ensure_ascii=False, indent=2))
        self.status.setText(
            f"Ready · {info['files']} files · {info['directories']} folders · "
            f"{'Git repository' if info['git_repository'] else 'no Git repository'}"
        )

    def start(self) -> None:
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        pair = self.pair.text().strip().upper()
        if len(pair) < 8:
            QMessageBox.warning(self, "Pairing", "Enter the pairing code returned by TriForce workspace_status.")
            return
        try:
            runtime = self.runtime()
            self.details.setPlainText(json.dumps(runtime.analyze(), ensure_ascii=False, indent=2))
        except Exception as exc:
            QMessageBox.warning(self, "Workspace", str(exc))
            return

        args = [
            "-m", "local_workspace_client.client",
            "--workspace", str(runtime.root),
            "--pair", pair,
            "--task", runtime.task,
        ]
        args.append("--write" if runtime.writable else "--read-only")
        self.proc = QProcess(self)
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(args)
        self.proc.setWorkingDirectory(str(Path(__file__).resolve().parents[1]))
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self.read_stdout)
        self.proc.readyReadStandardError.connect(self.read_stderr)
        self.proc.finished.connect(self.finished)
        self.proc.start()
        if not self.proc.waitForStarted(3000):
            QMessageBox.critical(self, "TriForce Workspace", "Could not start the local workspace node")
            self.proc = None
            return
        self.status.setText("Connecting to TriForce…")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pair.setEnabled(False)
        self.folder.setEnabled(False)
        self.read_only.setEnabled(False)
        self.write.setEnabled(False)

    def read_stdout(self) -> None:
        if not self.proc:
            return
        raw = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "connected":
                mode = "Write" if event.get("mode") == "write" else "Read only"
                self.status.setText(f"Paired · {mode} · keep this window open")
                analysis = event.get("analysis")
                if isinstance(analysis, dict):
                    self.details.setPlainText(json.dumps(analysis, ensure_ascii=False, indent=2))

    def read_stderr(self) -> None:
        if not self.proc:
            return
        text = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if text:
            self.details.appendPlainText("\n" + text)
            self.status.setText("Connection error")

    def stop(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            if not self.proc.waitForFinished(2500):
                self.proc.kill()
        self.reset_controls("Stopped · local workspace disconnected")

    def finished(self, exit_code: int, _status) -> None:
        self.reset_controls("Stopped · local workspace disconnected" if exit_code == 0 else f"Stopped with error ({exit_code})")

    def reset_controls(self, message: str) -> None:
        self.status.setText(message)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pair.setEnabled(True)
        self.folder.setEnabled(True)
        self.read_only.setEnabled(True)
        self.write.setEnabled(True)
        self.proc = None

    def copy_url(self) -> None:
        QApplication.clipboard().setText(self.url.text())
        self.status.setText("Public MCP URL copied")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop()
        event.accept()


def _pair_from_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    return (parse_qs(parsed.query).get("code") or [""])[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pair", default="")
    parser.add_argument("--pair-url", default="")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    pair = args.pair or _pair_from_url(args.pair_url)
    app = QApplication([sys.argv[0], *qt_args])
    window = WorkspaceWindow(pair)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
