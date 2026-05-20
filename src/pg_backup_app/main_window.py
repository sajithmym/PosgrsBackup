from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import QSettings, QThread, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .backup_service import BackupService
from .db import DbConnectionConfig, connect_to_database


class OperationWorker(QThread):
    progress = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation: Callable[[Callable[[str], None]], object]) -> None:
        super().__init__()
        self._operation = operation

    def run(self) -> None:
        try:
            result = self._operation(self.progress.emit)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    THEME_SYSTEM = "system"
    THEME_LIGHT = "light"
    THEME_DARK = "dark"

    def __init__(self) -> None:
        super().__init__()
        self._service = BackupService()
        self._settings = QSettings("PosgrsBackup", "PostgreSQLBackupRestore")
        self._worker: OperationWorker | None = None
        self._last_backup_root: Path | None = None
        self.setWindowTitle("PostgreSQL Backup and Restore")
        self.setWindowIcon(self._create_app_icon())
        self.setMinimumSize(980, 700)
        self.resize(1120, 760)
        self._build_ui()
        self._load_settings()
        self._apply_selected_theme()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 20, 28, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(18)
        header_text = QVBoxLayout()
        header_text.setSpacing(6)
        title = QLabel("PostgreSQL Backup and Restore")
        title.setObjectName("Title")
        subtitle = QLabel(
            "Create structured CSV/SQL backups, restore generated backup folders, and keep your workspace settings ready for the next run."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        header_text.addWidget(title)
        header_text.addWidget(subtitle)
        header.addLayout(header_text, 1)

        theme_layout = QVBoxLayout()
        theme_layout.setSpacing(4)
        theme_label = QLabel("Theme")
        theme_label.setObjectName("SmallLabel")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("System", self.THEME_SYSTEM)
        self.theme_combo.addItem("Light", self.THEME_LIGHT)
        self.theme_combo.addItem("Dark", self.THEME_DARK)
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        theme_layout.addWidget(theme_label)
        theme_layout.addWidget(self.theme_combo)
        header.addLayout(theme_layout)
        layout.addLayout(header)

        top_grid = QGridLayout()
        top_grid.setColumnStretch(0, 1)
        top_grid.setColumnStretch(1, 1)
        top_grid.setHorizontalSpacing(16)
        top_grid.setVerticalSpacing(12)

        connection_card = self._card("Connection")
        connection_grid = QGridLayout()
        connection_grid.setHorizontalSpacing(16)
        connection_grid.setVerticalSpacing(10)

        self.host_input = QLineEdit("localhost")
        self.port_input = QLineEdit("5432")
        self.user_input = QLineEdit("postgres")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.database_input = QLineEdit()
        self.database_input.setPlaceholderText("Database name")

        self._add_input_row(connection_grid, 0, 0, "Host", self.host_input)
        self._add_input_row(connection_grid, 1, 0, "Port", self.port_input)
        self._add_input_row(connection_grid, 2, 0, "User", self.user_input)
        self._add_input_row(connection_grid, 0, 2, "Password", self.password_input)
        self._add_input_row(connection_grid, 1, 2, "Database", self.database_input)

        self.test_button = QPushButton("Test Connection")
        self.test_button.setObjectName("OutlineButton")
        self.test_button.clicked.connect(self._start_test_connection)
        connection_grid.addWidget(self.test_button, 2, 3)
        connection_grid.setColumnStretch(1, 1)
        connection_grid.setColumnStretch(3, 1)
        connection_card.layout().addLayout(connection_grid)
        top_grid.addWidget(connection_card, 0, 0)

        folder_card = self._card("Folders")
        folder_layout = QVBoxLayout()
        folder_layout.setSpacing(14)

        self.backup_parent_input = QLineEdit()
        self.backup_parent_input.setPlaceholderText("Choose where new backup folders are created")
        backup_parent_row = self._path_row(
            self.backup_parent_input,
            "Save Folder",
            self._select_backup_parent,
        )
        folder_layout.addLayout(backup_parent_row)

        self.restore_folder_input = QLineEdit()
        self.restore_folder_input.setPlaceholderText("Choose an existing generated backup folder")
        restore_folder_row = self._path_row(
            self.restore_folder_input,
            "Backup Folder",
            self._select_restore_folder,
        )
        folder_layout.addLayout(restore_folder_row)

        self.open_backup_button = QPushButton("Open Last Backup")
        self.open_backup_button.setObjectName("OutlineButton")
        self.open_backup_button.clicked.connect(self._open_last_backup_folder)
        self.open_backup_button.setEnabled(False)
        folder_layout.addWidget(self.open_backup_button)
        folder_card.layout().addLayout(folder_layout)
        top_grid.addWidget(folder_card, 0, 1)
        layout.addLayout(top_grid)

        action_grid = QGridLayout()
        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        action_grid.setHorizontalSpacing(16)
        action_grid.setVerticalSpacing(0)
        self.backup_button = QPushButton("Backup Database")
        self.backup_button.clicked.connect(self._start_backup)
        self.restore_button = QPushButton("Restore Database")
        self.restore_button.setObjectName("SecondaryButton")
        self.restore_button.clicked.connect(self._start_restore)
        action_grid.addWidget(self.backup_button, 0, 0)
        action_grid.addWidget(self.restore_button, 0, 1)
        layout.addLayout(action_grid)

        status_card = self._card("Status")
        status_layout = QVBoxLayout()
        status_layout.setSpacing(10)
        status_header = QHBoxLayout()
        status_header.setSpacing(10)
        self.summary_label = QLabel("Ready")
        self.summary_label.setObjectName("SummaryLabel")
        self.copy_log_button = QPushButton("Copy Status")
        self.copy_log_button.clicked.connect(self._copy_status)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("GhostButton")
        self.clear_button.clicked.connect(lambda: self.status_output.clear())
        status_header.addWidget(self.summary_label, 1)
        status_header.addWidget(self.copy_log_button)
        status_header.addWidget(self.clear_button)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_output = QTextEdit()
        self.status_output.setReadOnly(True)
        self.status_output.setMinimumHeight(165)
        self.status_output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.status_output.setPlaceholderText("Backup and restore progress will appear here.")
        status_layout.addLayout(status_header)
        status_layout.addWidget(self.progress_bar)
        status_layout.addWidget(self.status_output, 1)
        status_card.layout().addLayout(status_layout)
        layout.addWidget(status_card, 1)

        self.setCentralWidget(root)

    def _path_row(self, line_edit: QLineEdit, button_text: str, handler: Callable[[], None]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        button = QPushButton(button_text)
        button.clicked.connect(handler)
        row.addWidget(line_edit, 1)
        row.addWidget(button)
        return row

    def _card(self, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        return card

    def _add_input_row(
        self,
        layout: QGridLayout,
        row: int,
        column: int,
        label_text: str,
        field: QLineEdit,
    ) -> None:
        label = QLabel(label_text)
        label.setObjectName("FieldLabel")
        layout.addWidget(label, row, column)
        layout.addWidget(field, row, column + 1)

    def _create_app_icon(self) -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1769e0"))
        painter.drawRoundedRect(10, 16, 44, 34, 6, 6)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(14, 10, 36, 14)
        painter.setBrush(QColor("#d8e8ff"))
        painter.drawEllipse(16, 13, 32, 8)
        painter.setPen(QPen(QColor("#ffffff"), 4))
        painter.drawLine(24, 32, 40, 32)
        painter.drawLine(36, 27, 42, 32)
        painter.drawLine(36, 37, 42, 32)
        painter.end()
        return QIcon(pixmap)

    def _load_settings(self) -> None:
        self.host_input.setText(self._settings.value("connection/host", "localhost"))
        self.port_input.setText(self._settings.value("connection/port", "5432"))
        self.user_input.setText(self._settings.value("connection/user", "postgres"))
        self.database_input.setText(self._settings.value("connection/database", ""))
        self.backup_parent_input.setText(self._settings.value("folders/backup_parent", ""))
        self.restore_folder_input.setText(self._settings.value("folders/restore_folder", ""))
        last_backup = self._settings.value("folders/last_backup", "")
        if last_backup:
            self._last_backup_root = Path(last_backup)
            self.open_backup_button.setEnabled(self._last_backup_root.exists())

        theme = self._settings.value("ui/theme", self.THEME_SYSTEM)
        index = self.theme_combo.findData(theme)
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)

    def _save_settings(self) -> None:
        self._settings.setValue("connection/host", self.host_input.text())
        self._settings.setValue("connection/port", self.port_input.text())
        self._settings.setValue("connection/user", self.user_input.text())
        self._settings.setValue("connection/database", self.database_input.text())
        self._settings.setValue("folders/backup_parent", self.backup_parent_input.text())
        self._settings.setValue("folders/restore_folder", self.restore_folder_input.text())
        if self._last_backup_root:
            self._settings.setValue("folders/last_backup", str(self._last_backup_root))
        self._settings.sync()

    def _theme_changed(self) -> None:
        theme = self.theme_combo.currentData()
        self._settings.setValue("ui/theme", theme)
        self._settings.sync()
        self._apply_selected_theme()

    def _apply_selected_theme(self) -> None:
        theme = self.theme_combo.currentData() or self.THEME_SYSTEM
        if theme == self.THEME_SYSTEM:
            theme = self.THEME_DARK if self._system_prefers_dark() else self.THEME_LIGHT
        self.setStyleSheet(self._dark_stylesheet() if theme == self.THEME_DARK else self._light_stylesheet())

    def _system_prefers_dark(self) -> bool:
        if sys.platform.startswith("win"):
            windows_value = self._windows_apps_use_light_theme()
            if windows_value is not None:
                return windows_value == 0
        return QApplication.palette().window().color().lightness() < 128

    def _windows_apps_use_light_theme(self) -> int | None:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value)
        except OSError:
            return None

    def _select_backup_parent(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Save Folder", self.backup_parent_input.text())
        if folder:
            self.backup_parent_input.setText(folder)
            self._save_settings()

    def _select_restore_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Backup Folder", self.restore_folder_input.text())
        if folder:
            self.restore_folder_input.setText(folder)
            self._save_settings()

    def _start_test_connection(self) -> None:
        config = self._read_config()
        if not config:
            return
        self._save_settings()

        def operation(progress: Callable[[str], None]) -> str:
            progress("Testing PostgreSQL connection...")
            with connect_to_database(config):
                pass
            return "Connection test successful."

        self._run_operation(operation, "Testing connection...")

    def _start_backup(self) -> None:
        config = self._read_config()
        if not config:
            return

        destination = self.backup_parent_input.text().strip()
        if not destination:
            self._select_backup_parent()
            destination = self.backup_parent_input.text().strip()
        if not destination:
            return
        self._save_settings()

        def operation(progress: Callable[[str], None]) -> tuple[str, Path]:
            backup_root = self._service.backup_database(config, Path(destination), progress)
            return f"Backup completed successfully.\nFolder: {backup_root}", backup_root

        self._run_operation(operation, "Running backup...")

    def _start_restore(self) -> None:
        config = self._read_config()
        if not config:
            return

        backup_folder = self.restore_folder_input.text().strip()
        if not backup_folder:
            self._select_restore_folder()
            backup_folder = self.restore_folder_input.text().strip()
        if not backup_folder:
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Restore",
            "Restore will insert backup data into the selected database. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._save_settings()

        def operation(progress: Callable[[str], None]) -> str:
            self._service.restore_database(config, Path(backup_folder), progress)
            return "Restore completed successfully."

        self._run_operation(operation, "Running restore...")

    def _run_operation(
        self,
        operation: Callable[[Callable[[str], None]], object],
        summary: str,
    ) -> None:
        self._set_busy(True)
        self.progress_bar.setRange(0, 0)
        self.summary_label.setText(summary)
        self._append_status(summary)
        self._worker = OperationWorker(operation)
        self._worker.progress.connect(self._append_status)
        self._worker.succeeded.connect(self._operation_succeeded)
        self._worker.failed.connect(self._operation_failed)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _operation_succeeded(self, result: object) -> None:
        message = str(result)
        if isinstance(result, tuple) and len(result) == 2:
            message = str(result[0])
            self._last_backup_root = Path(result[1])
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.summary_label.setText("Completed")
        self._append_status(message)
        self._save_settings()
        self.open_backup_button.setEnabled(bool(self._last_backup_root and self._last_backup_root.exists()))

    def _operation_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.summary_label.setText("Failed")
        self._append_status(f"Error: {message}")
        QMessageBox.critical(self, "Operation Failed", message)

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self.backup_button,
            self.restore_button,
            self.test_button,
            self.open_backup_button,
            self.copy_log_button,
            self.clear_button,
        ):
            button.setDisabled(busy)
        if not busy:
            self.open_backup_button.setEnabled(bool(self._last_backup_root and self._last_backup_root.exists()))

    def _append_status(self, message: str) -> None:
        self.status_output.append(message)
        self.status_output.verticalScrollBar().setValue(self.status_output.verticalScrollBar().maximum())

    def _copy_status(self) -> None:
        QApplication.clipboard().setText(self.status_output.toPlainText())
        self.summary_label.setText("Status copied")

    def _open_last_backup_folder(self) -> None:
        if self._last_backup_root and self._last_backup_root.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_backup_root)))
        else:
            QMessageBox.information(self, "Open Last Backup", "No completed backup folder is available yet.")

    def _read_config(self) -> DbConnectionConfig | None:
        try:
            port = int(self.port_input.text())
            config = DbConnectionConfig(
                host=self.host_input.text(),
                port=port,
                user=self.user_input.text(),
                password=self.password_input.text(),
                database=self.database_input.text(),
            )
            config.validate()
            return config
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Connection", str(exc))
            return None

    def _base_stylesheet(
        self,
        *,
        background: str,
        panel: str,
        panel_alt: str,
        text: str,
        muted: str,
        border: str,
        field: str,
        primary: str,
        primary_hover: str,
        secondary: str,
        secondary_hover: str,
        status: str,
        disabled: str,
    ) -> str:
        return f"""
            QWidget {{
                background: {background};
                color: {text};
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 14px;
            }}
            #AppRoot {{
                background: {background};
            }}
            #Title {{
                font-size: 28px;
                font-weight: 700;
                color: {text};
            }}
            #Subtitle, #SmallLabel {{
                color: {muted};
                font-size: 14px;
            }}
            #SmallLabel {{
                font-weight: 600;
            }}
            #SectionTitle {{
                background: transparent;
                color: {text};
                font-size: 15px;
                font-weight: 700;
            }}
            #FieldLabel {{
                background: transparent;
                color: {muted};
                font-weight: 600;
                min-width: 78px;
            }}
            #SummaryLabel {{
                background: {panel_alt};
                color: {text};
                font-weight: 600;
                border-radius: 7px;
                padding: 8px 10px;
            }}
            QFrame#Card {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QLineEdit, QTextEdit, QComboBox {{
                background: {field};
                color: {text};
                border: 1px solid {border};
                border-radius: 8px;
                selection-background-color: {primary};
            }}
            QLineEdit, QComboBox {{
                min-height: 36px;
                padding: 4px 14px;
            }}
            QTextEdit {{
                background: {status};
                padding: 8px 10px;
                font-family: Consolas, Cascadia Mono, monospace;
                font-size: 13px;
            }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
                border: 1px solid {primary};
            }}
            QPushButton {{
                background: {primary};
                color: #ffffff;
                border: none;
                border-radius: 9px;
                min-height: 34px;
                padding: 8px 16px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {primary_hover};
            }}
            QPushButton:disabled {{
                background: {disabled};
                color: {muted};
            }}
            #SecondaryButton {{
                background: {secondary};
            }}
            #SecondaryButton:hover {{
                background: {secondary_hover};
            }}
            #GhostButton, #OutlineButton {{
                background: transparent;
                color: {primary};
                border: 1px solid {border};
            }}
            #GhostButton:hover, #OutlineButton:hover {{
                background: {panel_alt};
            }}
            #OutlineButton {{
                min-height: 36px;
            }}
            QProgressBar {{
                border: 1px solid {border};
                border-radius: 7px;
                background: {field};
                height: 18px;
                text-align: center;
                color: {text};
            }}
            QProgressBar::chunk {{
                background: {primary};
                border-radius: 7px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 28px;
            }}
            QComboBox QAbstractItemView {{
                background: {panel};
                color: {text};
                border: 1px solid {border};
                selection-background-color: {primary};
            }}
        """

    def _light_stylesheet(self) -> str:
        return self._base_stylesheet(
            background="#f3f6fb",
            panel="#ffffff",
            panel_alt="#f7f9fc",
            text="#101828",
            muted="#5b6778",
            border="#d4dce8",
            field="#ffffff",
            primary="#2563eb",
            primary_hover="#1d4ed8",
            secondary="#0f8b6b",
            secondary_hover="#0b755b",
            status="#ffffff",
            disabled="#e4eaf2",
        )

    def _dark_stylesheet(self) -> str:
        return self._base_stylesheet(
            background="#0f1623",
            panel="#151f2e",
            panel_alt="#101827",
            text="#eef4fb",
            muted="#9cadbf",
            border="#2b3a4f",
            field="#0d1421",
            primary="#4f8cff",
            primary_hover="#3b76e8",
            secondary="#15b98f",
            secondary_hover="#0fa77f",
            status="#0a101b",
            disabled="#243247",
        )


def run_app() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
