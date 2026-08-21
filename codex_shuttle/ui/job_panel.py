"""작업 탭 본체 — 좌측 잡 목록과 우측 대화 뷰."""

from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from codex_shuttle.core.account import ModelInfo
from codex_shuttle.core.job import ApprovalRequest, Job, JobItem, JobOrigin
from codex_shuttle.core.job_runner import JobRunner
from codex_shuttle.core import transcript
from codex_shuttle.ui import theme
from codex_shuttle.ui.approval_widget import ApprovalWidget
from codex_shuttle.ui.conversation_view import ConversationView
from codex_shuttle.ui.job_list import JobListWidget, STATE_LABELS
from codex_shuttle.ui.new_job_dialog import NewJobDialog

_LIST_WIDTH = 240


class JobPanel(QWidget):
    """잡을 제출하고, 진행 중인 대화를 관찰하고, 승인에 답하는 화면."""

    pendingApprovalsChanged = pyqtSignal(int)

    def __init__(self, runner: JobRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runner = runner
        self._models: tuple[ModelInfo, ...] = ()
        self._views: dict[str, ConversationView] = {}
        self._approvals: dict[object, ApprovalWidget] = {}
        self._current_job_id: str | None = None

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([_LIST_WIDTH, 600])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        runner.jobAdded.connect(self._on_job_added)
        runner.jobChanged.connect(self._on_job_changed)
        runner.itemChanged.connect(self._on_item_changed)
        runner.approvalRequested.connect(self._on_approval_requested)
        runner.approvalResolved.connect(self._on_approval_resolved)
        runner.jobRemoved.connect(self._on_job_removed)

    def _build_left(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(8, 8, 4, 8)
        layout.setSpacing(6)

        self._new_button = QPushButton("+ New job")
        self._new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_button.clicked.connect(self._on_new_job)
        layout.addWidget(self._new_button)

        self._list = JobListWidget()
        self._list.jobSelected.connect(self._on_job_selected)
        self._list.deleteRequested.connect(self._runner.forget)
        layout.addWidget(self._list, 1)

        self._clear_button = QPushButton("Clear finished")
        self._clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_button.setToolTip("Right-click a row to remove just that one")
        self._clear_button.clicked.connect(self._on_clear_finished)
        layout.addWidget(self._clear_button)
        return holder

    def _build_right(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(4, 8, 8, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(10, 0, 0, 0)
        self._header_title = QLabel()
        self._header_title.setStyleSheet("font-weight: 600;")
        header.addWidget(self._header_title)

        self._header_meta = QLabel()
        self._header_meta.setStyleSheet(
            "color: {0}; font-size: 11px;".format(theme.muted_color(self.palette()))
        )
        header.addWidget(self._header_meta)
        header.addStretch(1)

        self._save_button = QPushButton("Save transcript")
        self._save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_button.setToolTip("Save this job's full conversation to a file")
        self._save_button.clicked.connect(self._on_save_transcript)
        self._save_button.setEnabled(False)
        header.addWidget(self._save_button)

        self._interrupt = QPushButton("Stop")
        self._interrupt.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt.clicked.connect(self._on_interrupt)
        self._interrupt.setEnabled(False)
        header.addWidget(self._interrupt)
        layout.addLayout(header)

        self._stack = QStackedWidget()
        self._empty = QLabel("Pick a job on the left, or create a new one.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(
            "color: {0};".format(theme.muted_color(self.palette()))
        )
        self._stack.addWidget(self._empty)
        layout.addWidget(self._stack, 1)
        return holder

    def set_models(self, models: Sequence[ModelInfo]) -> None:
        """새 잡 대화상자의 모델 목록을 갱신한다."""
        self._models = tuple(models)

    def _on_new_job(self) -> None:
        dialog = NewJobDialog(self._models, default_cwd="", parent=self)
        if dialog.exec() != NewJobDialog.DialogCode.Accepted:
            return
        spec = dialog.spec()
        if not spec.prompt:
            QMessageBox.warning(self, "New job", "Enter the task first.")
            return
        # GUI에서 만든 잡은 클로드 세션에 노출되지 않는다.
        self._runner.submit(spec, origin=JobOrigin.LOCAL)

    def _on_job_added(self, job: Job) -> None:
        view = ConversationView()
        self._views[job.job_id] = view
        self._stack.addWidget(view)
        self._list.add_job(job)
        # 첫 잡이면 목록이 알아서 선택하므로 화면도 그쪽으로 맞춘다.
        if self._current_job_id is None:
            self._show_job(job.job_id)

    def _on_job_changed(self, job: Job) -> None:
        self._list.update_job(job)
        if job.job_id == self._current_job_id:
            self._update_header(job)
        self._refresh_attention()

    def _on_item_changed(self, job: Job, item: JobItem) -> None:
        view = self._views.get(job.job_id)
        if view is not None:
            view.apply_item(item)

    def _on_approval_requested(self, job: Job, request: ApprovalRequest) -> None:
        view = self._views.get(job.job_id)
        if view is None:
            return
        widget = ApprovalWidget(request, job.spec.approval_timeout_sec)
        widget.decided.connect(self._runner.resolve_approval)
        self._approvals[request.request_id] = widget
        view.add_approval(widget, request.item_id)
        self._refresh_attention()

    def _on_approval_resolved(self, _job: Job, request: ApprovalRequest) -> None:
        widget = self._approvals.pop(request.request_id, None)
        if widget is not None and request.decision is not None:
            widget.mark_resolved(request.decision)
        self._refresh_attention()

    def _on_save_transcript(self) -> None:
        """지금 보고 있는 잡의 대화 전문을 파일로 저장한다."""
        job = self._runner.job(self._current_job_id or "")
        if job is None:
            return

        target, chosen = QFileDialog.getSaveFileName(
            self,
            "Save transcript",
            transcript.suggested_filename(job, "md"),
            "Markdown (*.md);;JSON (*.json)",
        )
        if not target:
            return

        path = Path(target)
        as_json = path.suffix.lower() == ".json" or chosen.startswith("JSON")
        if not path.suffix:
            path = path.with_suffix(".json" if as_json else ".md")

        body = transcript.to_json(job) if as_json else transcript.to_markdown(job)
        try:
            path.write_text(body, encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Save transcript", "Could not save.\n" + str(error))
            return
        self._save_button.setToolTip("Last saved: " + str(path))

    def _on_clear_finished(self) -> None:
        finished = [job for job in self._runner.jobs() if job.state.is_final]
        if not finished:
            return
        confirm = QMessageBox.question(
            self,
            "Clear finished",
            "Remove {0} finished job(s) from the list.\n"
            "Their transcripts go away too.".format(len(finished)),
        )
        if confirm is QMessageBox.StandardButton.Yes:
            self._runner.forget_finished()

    def _on_job_removed(self, job_id: str) -> None:
        view = self._views.pop(job_id, None)
        if view is not None:
            self._stack.removeWidget(view)
            view.deleteLater()
        self._list.remove_job(job_id)
        if self._current_job_id == job_id:
            self._current_job_id = None
            self._stack.setCurrentWidget(self._empty)
            self._header_title.clear()
            self._header_meta.clear()
            self._interrupt.setEnabled(False)
        self._refresh_attention()

    def _on_job_selected(self, job_id: str) -> None:
        self._show_job(job_id)

    def _show_job(self, job_id: str) -> None:
        view = self._views.get(job_id)
        if view is None:
            return
        self._current_job_id = job_id
        self._stack.setCurrentWidget(view)
        job = self._runner.job(job_id)
        if job is not None:
            self._update_header(job)
        # 보고 있는 잡은 더 이상 깜빡일 이유가 없다.
        self._refresh_attention()

    def _update_header(self, job: Job) -> None:
        self._header_title.setText(job.title)
        meta = [
            STATE_LABELS[job.state],
            self._model_label(job),
            self._effort_label(job),
            job.spec.sandbox,
            job.spec.approval_policy,
        ]
        self._header_meta.setText("  ·  " + " · ".join(meta))

    def _job_model(self, job: Job) -> ModelInfo | None:
        """이 잡에 적용되는 모델 정보. 지정이 없으면 기본 모델을 본다."""
        if job.spec.model:
            return next(
                (item for item in self._models if item.slug == job.spec.model), None
            )
        return next((item for item in self._models if item.is_default), None)

    def _model_label(self, job: Job) -> str:
        """지정을 생략해도 무엇으로 돌았는지 보이게 한다.

        괄호 안은 앱이 아는 기본 모델이다. codex가 실제로 무엇을 썼는지 돌려주지
        않으므로 확정값은 아니다.
        """
        if job.spec.model:
            return job.spec.model
        model = self._job_model(job)
        return "default ({0})".format(model.display_name) if model else "Default model"

    def _effort_label(self, job: Job) -> str:
        if job.spec.effort:
            return job.spec.effort
        model = self._job_model(job)
        if model is not None and model.default_effort:
            return "default ({0})".format(model.default_effort)
        return "Default effort"
        self._interrupt.setEnabled(not job.state.is_final)
        self._save_button.setEnabled(True)

    def _refresh_attention(self) -> None:
        """승인 대기 중인 잡 중 지금 보고 있지 않은 것만 깜빡이게 한다."""
        total = 0
        for job in self._runner.jobs():
            pending = len(job.pending_approvals)
            total += pending
            needs = pending > 0 and job.job_id != self._current_job_id
            self._list.set_flashing(job.job_id, needs)
        self.pendingApprovalsChanged.emit(total)

    def _on_interrupt(self) -> None:
        if self._current_job_id:
            self._runner.interrupt(self._current_job_id)
