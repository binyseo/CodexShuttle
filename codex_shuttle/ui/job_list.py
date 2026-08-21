"""좌측 잡 목록."""

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from codex_shuttle.core.job import Job, JobState
from codex_shuttle.core.status import CheckStatus
from codex_shuttle.ui import theme
from codex_shuttle.ui.attention import AttentionFlasher

_DOT_SIZE = 8

_STATE_STATUS = {
    JobState.QUEUED: CheckStatus.UNKNOWN,
    JobState.STARTING: CheckStatus.CHECKING,
    JobState.RUNNING: CheckStatus.CHECKING,
    JobState.SUCCEEDED: CheckStatus.OK,
    JobState.FAILED: CheckStatus.ERROR,
    JobState.INTERRUPTED: CheckStatus.WARNING,
}

STATE_LABELS = {
    JobState.QUEUED: "Queued",
    JobState.STARTING: "Starting",
    JobState.RUNNING: "Running",
    JobState.SUCCEEDED: "Succeeded",
    JobState.FAILED: "Failed",
    JobState.INTERRUPTED: "Interrupted",
}


def format_elapsed(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return "{0}s".format(total)
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return "{0}m {1}s".format(minutes, rest)
    hours, minutes = divmod(minutes, 60)
    return "{0}h {1}m".format(hours, minutes)


class JobListRow(QWidget):
    """잡 하나를 나타내는 목록 행."""

    def __init__(self, job: Job, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("JobListRow")
        self._muted = theme.muted_color(self.palette())
        self._flashing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)

        self._dot = QLabel()
        self._dot.setFixedSize(_DOT_SIZE, _DOT_SIZE)
        layout.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)

        self._title = QLabel()
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        texts.addWidget(self._title)

        self._subtitle = QLabel()
        self._subtitle.setStyleSheet("color: {0}; font-size: 11px;".format(self._muted))
        texts.addWidget(self._subtitle)

        layout.addLayout(texts, 1)
        self.update_job(job)

    def update_job(self, job: Job) -> None:
        status = _STATE_STATUS[job.state]
        color = theme.status_color(status, self.palette())
        self._dot.setStyleSheet(
            "background-color: {0}; border-radius: {1}px;".format(color, _DOT_SIZE // 2)
        )
        self._title.setText(job.title)

        # 어느 클로드 세션이 낸 잡인지 먼저 보여 준다. 세션을 여러 개 띄워 두면
        # 목록에서 구분이 되어야 헷갈리지 않는다.
        parts = [
            job.client_id or "Claude",
            STATE_LABELS[job.state],
            format_elapsed(job.elapsed_sec),
        ]
        if job.pending_approvals:
            parts.append("{0} awaiting approval".format(len(job.pending_approvals)))
        self._subtitle.setText(" · ".join(parts))

        pending_color = theme.status_color(CheckStatus.WARNING, self.palette())
        self._subtitle.setStyleSheet(
            "color: {0}; font-size: 11px;".format(
                pending_color if job.pending_approvals else self._muted
            )
        )

    def set_flashing(self, on: bool) -> None:
        """승인 대기를 알리는 배경 강조."""
        if on == self._flashing:
            return
        self._flashing = on
        if not on:
            self.setStyleSheet("")
            return
        accent = theme.status_color(CheckStatus.WARNING, self.palette())
        self.setStyleSheet(
            "QWidget#JobListRow {{ background-color: {0}; border-radius: 6px; }}".format(
                theme.with_alpha(accent, 60)
            )
        )


class JobListWidget(QListWidget):
    """잡 목록. 진행 중인 잡의 경과 시간을 1초마다 갱신한다."""

    jobSelected = pyqtSignal(str)
    deleteRequested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setSpacing(2)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._rows: dict[str, JobListRow] = {}
        self._items: dict[str, QListWidgetItem] = {}
        self._jobs: dict[str, Job] = {}
        self._flashers: dict[str, AttentionFlasher] = {}

        self.currentItemChanged.connect(self._on_current_changed)

        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()

    def add_job(self, job: Job) -> None:
        row = JobListRow(job)
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, row.sizeHint().height()))
        item.setData(Qt.ItemDataRole.UserRole, job.job_id)

        # 최신 잡이 맨 위로 온다. 오래 켜 두면 목록이 길어지는데, 방금 던진
        # 잡을 찾으려고 아래로 스크롤하지 않아도 된다.
        self.insertItem(0, item)
        self.setItemWidget(item, row)
        self._rows[job.job_id] = row
        self._items[job.job_id] = item
        self._jobs[job.job_id] = job

        if self.currentItem() is None:
            self.setCurrentItem(item)

    def update_job(self, job: Job) -> None:
        self._jobs[job.job_id] = job
        row = self._rows.get(job.job_id)
        if row is not None:
            row.update_job(job)

    def remove_job(self, job_id: str) -> None:
        """보관 한도를 넘겨 버려진 잡을 목록에서 지운다."""
        self._rows.pop(job_id, None)
        self._jobs.pop(job_id, None)
        flasher = self._flashers.pop(job_id, None)
        if flasher is not None:
            flasher.stop()
        item = self._items.pop(job_id, None)
        if item is not None:
            self.takeItem(self.row(item))

    def set_flashing(self, job_id: str, active: bool) -> None:
        row = self._rows.get(job_id)
        if row is None:
            return
        flasher = self._flashers.get(job_id)
        if flasher is None:
            flasher = AttentionFlasher(row.set_flashing, self)
            self._flashers[job_id] = flasher
        flasher.set_active(active)

    def current_job_id(self) -> str | None:
        item = self.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def select(self, job_id: str) -> None:
        item = self._items.get(job_id)
        if item is not None:
            self.setCurrentItem(item)

    def _on_context_menu(self, position) -> None:
        item = self.itemAt(position)
        if item is None:
            return
        job_id = item.data(Qt.ItemDataRole.UserRole)
        job = self._jobs.get(job_id)

        menu = QMenu(self)
        delete = menu.addAction("Remove")
        # 진행 중인 잡은 지우지 않는다. 먼저 중단해야 한다.
        finished = bool(job and job.state.is_final)
        delete.setEnabled(finished)
        if not finished:
            delete.setToolTip("Stop the job before removing it")

        if menu.exec(self.viewport().mapToGlobal(position)) is delete and finished:
            self.deleteRequested.emit(job_id)

    def _on_current_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        self.jobSelected.emit(current.data(Qt.ItemDataRole.UserRole))

    def _tick(self) -> None:
        for job_id, job in self._jobs.items():
            if job.state.is_final:
                continue
            row = self._rows.get(job_id)
            if row is not None:
                row.update_job(job)
