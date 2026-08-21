"""메인 윈도우 — 환경 탭과 작업 탭."""

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QKeySequence
from PyQt6.QtWidgets import QLabel, QMainWindow, QTabWidget

from codex_shuttle import APP_NAME
from codex_shuttle.core.account import AccountInfo, ModelsInfo, RateLimitInfo
from codex_shuttle.core.codex_cli import CodexCliInfo
from codex_shuttle.core.environment import EnvironmentMonitor
from codex_shuttle.core.ipc import LocalJobServer
from codex_shuttle.core.job_runner import JobRunner
from codex_shuttle.core.status import CheckStatus
from codex_shuttle.ui import theme
from codex_shuttle.ui.attention import AttentionFlasher
from codex_shuttle.ui.environment_panel import EnvironmentPanel
from codex_shuttle.ui.job_panel import JobPanel

_JOBS_INDEX = 1


class MainWindow(QMainWindow):
    """환경 점검과 잡 관찰을 탭으로 나눠 담는 창."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(960, 780)

        self._monitor = EnvironmentMonitor(self)
        self._runner = JobRunner(self._monitor.client, self)
        self._pending_approvals = 0
        self._summary_parts: dict[str, tuple[CheckStatus, str]] = {}

        self._environment = EnvironmentPanel()
        self._environment.refresh_button.clicked.connect(self._monitor.refresh)
        self._jobs = JobPanel(self._runner)
        self._jobs.pendingApprovalsChanged.connect(self._on_pending_changed)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._environment, "Environment")
        self._tabs.addTab(self._jobs, "Jobs")
        self._tabs.currentChanged.connect(lambda _index: self._update_job_tab())
        self.setCentralWidget(self._tabs)

        self._tab_flasher = AttentionFlasher(self._apply_tab_flash, self)

        self._ipc = LocalJobServer(self._runner, self._monitor, self)
        self._ipc.focusRequested.connect(self._bring_to_front)

        self._build_status_bar()
        self._connect_monitor()
        self._install_shortcut()

        # 창이 뜬 뒤에 시작해야 확인 중 상태가 화면에 보인다.
        QTimer.singleShot(0, self._monitor.refresh)

    def _build_status_bar(self) -> None:
        self._summary = QLabel()
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        self.statusBar().addWidget(self._summary)
        self._stamp_label = QLabel("Ready")
        self._stamp_label.setStyleSheet(
            "color: {0};".format(theme.muted_color(self.palette()))
        )
        self.statusBar().addPermanentWidget(self._stamp_label)
        self._render_summary()

    def _connect_monitor(self) -> None:
        self._monitor.cliChanged.connect(self._on_cli)
        self._monitor.accountChanged.connect(self._on_account)
        self._monitor.modelsChanged.connect(self._on_models)
        self._monitor.rateLimitsChanged.connect(self._on_rate_limits)
        # 잡 도중 인증이 풀리면 다음 재검사를 기다리지 않고 바로 카드에 반영한다.
        self._runner.authExpired.connect(self._monitor.mark_account_expired)

    def _install_shortcut(self) -> None:
        action = QAction("Recheck", self)
        action.setShortcut(QKeySequence("F5"))
        action.triggered.connect(self._monitor.refresh)
        self.addAction(action)

    def _on_cli(self, info: CodexCliInfo) -> None:
        self._environment.apply_cli(info)
        self._environment.refresh_button.setEnabled(
            info.status is not CheckStatus.CHECKING
        )
        self._set_summary("CLI", info.status, info.version or info.headline)

    def _on_account(self, info: AccountInfo) -> None:
        self._environment.apply_account(info)
        self._set_summary("Sign-in", info.status, info.headline)

    def _on_models(self, info: ModelsInfo) -> None:
        self._environment.apply_models(info)
        self._jobs.set_models(info.models)

    def _on_rate_limits(self, info: RateLimitInfo) -> None:
        self._environment.apply_rate_limits(info)
        self._set_summary("Limit", info.status, info.headline)

    def _set_summary(self, key: str, status: CheckStatus, text: str) -> None:
        self._summary_parts[key] = (status, text)
        self._render_summary()
        if status.is_settled:
            self._stamp_label.setText(
                "Updated " + datetime.now().strftime("%H:%M:%S")
            )

    def _render_summary(self) -> None:
        """상태바에 환경 요약을 한 줄로 상주시킨다.

        작업 탭을 보고 있어도 잔여 한도를 확인하려고 탭을 오갈 필요가 없게 한다.
        """
        if not self._summary_parts:
            self._summary.setText("Checking environment…")
            return
        chunks = []
        for key in ("CLI", "Sign-in", "Limit"):
            entry = self._summary_parts.get(key)
            if entry is None:
                continue
            status, text = entry
            color = theme.status_color(status, self.palette())
            chunks.append(
                '<span style="color:{0}">&#9679;</span> {1} {2}'.format(
                    color, key, text
                )
            )
        self._summary.setText("&nbsp;&nbsp;&nbsp;".join(chunks))

    def _on_pending_changed(self, count: int) -> None:
        self._pending_approvals = count
        self._update_job_tab()

    def _update_job_tab(self) -> None:
        """승인 대기가 있고 작업 탭을 보고 있지 않으면 탭을 깜빡인다."""
        label = "Jobs"
        if self._pending_approvals:
            label = "Jobs {0}".format(self._pending_approvals)
        self._tabs.setTabText(_JOBS_INDEX, label)

        away = self._tabs.currentIndex() != _JOBS_INDEX
        self._tab_flasher.set_active(bool(self._pending_approvals) and away)

    def _apply_tab_flash(self, on: bool) -> None:
        color = (
            QColor(theme.status_color(CheckStatus.WARNING, self.palette()))
            if on
            else QColor()
        )
        self._tabs.tabBar().setTabTextColor(_JOBS_INDEX, color)

    def start_ipc(self) -> bool:
        """CLI 창구를 연다. 다른 인스턴스가 이미 떠 있으면 False."""
        return self._ipc.start()

    def _bring_to_front(self) -> None:
        """두 번째 인스턴스가 실행됐을 때 기존 창을 앞으로 올린다."""
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized
            | Qt.WindowState.WindowActive
        )
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        self._ipc.stop()
        self._monitor.shutdown()
        super().closeEvent(event)
