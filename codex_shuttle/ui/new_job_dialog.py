"""잡을 손으로 제출하는 대화상자.

HTTP 창구가 붙기 전까지 잡을 돌려 보는 수단이고, 붙은 뒤에도 사람이 직접 잡을
넣을 때 그대로 쓴다.
"""

from collections.abc import Sequence

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from codex_shuttle.core.account import ModelInfo
from codex_shuttle.core.job import (
    APPROVAL_NEVER,
    APPROVAL_ON_REQUEST,
    APPROVAL_UNTRUSTED,
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    ApprovalDecision,
    JobSpec,
)
from codex_shuttle.core.status import CheckStatus
from codex_shuttle.ui import theme

_SANDBOXES = (
    ("Workspace write (workspace-write)", SANDBOX_WORKSPACE_WRITE),
    ("Read only (read-only)", SANDBOX_READ_ONLY),
    ("Unrestricted (danger-full-access)", SANDBOX_DANGER_FULL_ACCESS),
)

_POLICIES = (
    ("Never ask (never)", APPROVAL_NEVER),
    ("Ask when needed (on-request)", APPROVAL_ON_REQUEST),
    ("Ask for untrusted commands (untrusted)", APPROVAL_UNTRUSTED),
)

# (표시 문구, 일반 승인 결정, 권한 상승 결정). 권한 상승 결정이 None이면 종류를
# 가리지 않고 같은 결정을 쓴다.
_TIMEOUT_ACTIONS = (
    ("Deny and continue", ApprovalDecision.DECLINE, None),
    ("Deny and stop turn", ApprovalDecision.CANCEL, None),
    ("Allow", ApprovalDecision.ACCEPT, None),
    ("Allow for session", ApprovalDecision.ACCEPT_FOR_SESSION, None),
    ("Allow, deny elevation", ApprovalDecision.ACCEPT, ApprovalDecision.DECLINE),
    (
        "Allow for session, deny elevation",
        ApprovalDecision.ACCEPT_FOR_SESSION,
        ApprovalDecision.DECLINE,
    ),
)

# 자동 허용을 고르면 대기 시간이 그대로 죽은 시간이 된다. 짧게 쓰라고 안내한다.
_AUTO_APPROVE_HINT = (
    "Unattended auto-allow. Keep the wait short (15-30s)."
)


class NewJobDialog(QDialog):
    """잡 제출에 필요한 값들을 받는다."""

    def __init__(
        self,
        models: Sequence[ModelInfo] = (),
        default_cwd: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New job")
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self._prompt = QPlainTextEdit()
        self._prompt.setPlaceholderText("Describe the task for codex.")
        self._prompt.setMinimumHeight(140)
        form.addRow("Task", self._prompt)

        self._label = QLineEdit()
        self._label.setPlaceholderText("Defaults to the first line of the task")
        form.addRow("Label", self._label)

        self._cwd = QLineEdit(default_cwd)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._pick_cwd)
        cwd_row = QHBoxLayout()
        cwd_row.addWidget(self._cwd, 1)
        cwd_row.addWidget(browse)
        cwd_holder = QWidget()
        cwd_holder.setLayout(cwd_row)
        cwd_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Working folder", cwd_holder)

        self._model = QComboBox()
        self._model.addItem("Default model", "")
        for model in models:
            suffix = " (default)" if model.is_default else ""
            self._model.addItem(model.display_name + suffix, model.slug)
        form.addRow("Model", self._model)

        self._effort = QComboBox()
        self._effort.addItem("Default", "")
        for effort in ("low", "medium", "high", "xhigh", "max"):
            self._effort.addItem(effort, effort)
        form.addRow("Reasoning effort", self._effort)

        self._sandbox = QComboBox()
        for text, value in _SANDBOXES:
            self._sandbox.addItem(text, value)
        form.addRow("Sandbox", self._sandbox)

        self._policy = QComboBox()
        for text, value in _POLICIES:
            self._policy.addItem(text, value)
        form.addRow("Approval policy", self._policy)

        self._timeout = QSpinBox()
        self._timeout.setRange(5, 3600)
        self._timeout.setValue(300)
        self._timeout.setSuffix("s")
        form.addRow("Approval wait", self._timeout)

        self._timeout_action = QComboBox()
        for text, decision, permission_decision in _TIMEOUT_ACTIONS:
            self._timeout_action.addItem(text, (decision, permission_decision))
        self._timeout_action.currentIndexChanged.connect(self._on_timeout_action)
        form.addRow("On timeout", self._timeout_action)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            "color: {0}; font-size: 11px;".format(
                theme.status_color(CheckStatus.WARNING, self.palette())
            )
        )
        self._hint.hide()
        form.addRow("", self._hint)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Submit")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_timeout_action(self, _index: int) -> None:
        decision, _permission = self._timeout_action.currentData()
        auto_approve = decision in (
            ApprovalDecision.ACCEPT,
            ApprovalDecision.ACCEPT_FOR_SESSION,
        )
        self._hint.setText(_AUTO_APPROVE_HINT if auto_approve else "")
        self._hint.setVisible(auto_approve)

    def _pick_cwd(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose working folder", self._cwd.text()
        )
        if chosen:
            self._cwd.setText(chosen)

    def spec(self) -> JobSpec:
        """입력값을 JobSpec으로 옮긴다."""
        timeout_decision, permission_decision = self._timeout_action.currentData()
        return JobSpec(
            prompt=self._prompt.toPlainText().strip(),
            label=self._label.text().strip(),
            cwd=self._cwd.text().strip() or None,
            model=self._model.currentData() or None,
            effort=self._effort.currentData() or None,
            sandbox=self._sandbox.currentData(),
            approval_policy=self._policy.currentData(),
            approval_timeout_sec=self._timeout.value(),
            approval_timeout_decision=timeout_decision,
            permission_timeout_decision=permission_decision,
        )
