"""대화 뷰 안에 인라인으로 뜨는 승인 위젯."""

import json
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from codex_shuttle.core.job import ApprovalDecision, ApprovalRequest
from codex_shuttle.core.status import CheckStatus
from codex_shuttle.ui import theme

_KIND_LABELS = {
    "command": "Command approval",
    "fileChange": "File change approval",
    "permissions": "Elevated permission",
}

_BUTTONS = (
    ("Allow", ApprovalDecision.ACCEPT),
    ("Allow for session", ApprovalDecision.ACCEPT_FOR_SESSION),
    ("Deny", ApprovalDecision.DECLINE),
    ("Deny and stop turn", ApprovalDecision.CANCEL),
)

_DECISION_LABELS = {
    ApprovalDecision.ACCEPT: "Allowed",
    ApprovalDecision.ACCEPT_FOR_SESSION: "Allowed for session",
    ApprovalDecision.DECLINE: "Denied",
    ApprovalDecision.CANCEL: "Denied, turn stopped",
    ApprovalDecision.TIMED_OUT: "Auto-resolved after timeout",
}


def permission_lines(profile: object) -> list[str]:
    """권한 프로필을 사람이 읽을 줄 목록으로 편다.

    스키마를 다 알지 못하는 부분이 있어, 해석되지 않으면 원본 JSON을 그대로 보여
    준다. 사용자가 무엇을 허용하는지 모른 채 누르는 상황을 만들지 않기 위함이다.
    """
    if not isinstance(profile, dict) or not profile:
        return []

    lines: list[str] = []
    file_system = profile.get("fileSystem")
    if isinstance(file_system, dict):
        for key, label in (("read", "read"), ("write", "write")):
            values = file_system.get(key)
            if values:
                lines.append(
                    "File {0}: {1}".format(label, ", ".join(str(v) for v in values))
                )
        entries = file_system.get("entries")
        if entries:
            lines.append("{0} file entries".format(len(entries)))

    network = profile.get("network")
    if isinstance(network, dict) and network:
        rendered = False
        for key in ("hosts", "allow", "allowedHosts", "domains"):
            values = network.get(key)
            if values:
                lines.append("Network: " + ", ".join(str(v) for v in values))
                rendered = True
        if not rendered:
            lines.append("Network access requested")

    if not lines:
        lines.append(json.dumps(profile, ensure_ascii=False))
    return lines


class ApprovalWidget(QFrame):
    """승인 요청 하나를 보여 주고 결정을 받는다."""

    decided = pyqtSignal(object, object)  # request_id, ApprovalDecision

    def __init__(
        self,
        request: ApprovalRequest,
        timeout_sec: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ApprovalWidget")
        self._request = request
        self._deadline = request.created_at + max(1, timeout_sec)
        self._resolved = False

        palette = self.palette()
        accent = theme.status_color(CheckStatus.WARNING, palette)
        muted = theme.muted_color(palette)
        self.setStyleSheet(
            "QFrame#ApprovalWidget {{ border: 1px solid {0}; border-radius: 8px;"
            " background-color: palette(base); }}".format(accent)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        kind = QLabel(_KIND_LABELS.get(request.kind, "Approval required"))
        kind.setStyleSheet("color: {0}; font-weight: 600;".format(accent))
        header.addWidget(kind)
        header.addStretch(1)
        self._countdown = QLabel()
        self._countdown.setStyleSheet("color: " + muted + "; font-size: 11px;")
        header.addWidget(self._countdown)
        layout.addLayout(header)

        title = QLabel(request.title)
        title.setWordWrap(True)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if request.kind == "command":
            title.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(title)

        if request.reason:
            reason = QLabel(request.reason)
            reason.setWordWrap(True)
            reason.setStyleSheet("color: " + muted + "; font-size: 11px;")
            layout.addWidget(reason)

        for line in permission_lines(request.params.get("permissions")):
            entry = QLabel("· " + line)
            entry.setWordWrap(True)
            entry.setStyleSheet("font-size: 11px;")
            layout.addWidget(entry)

        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(6)
        self._buttons.addStretch(1)
        for text, decision in _BUTTONS:
            button = QPushButton(text)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, chosen=decision: self._decide(chosen)
            )
            self._buttons.addWidget(button)
        layout.addLayout(self._buttons)

        self._outcome = QLabel()
        self._outcome.setStyleSheet("color: " + muted + ";")
        self._outcome.hide()
        layout.addWidget(self._outcome)

        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._update_countdown)
        self._ticker.start()
        self._update_countdown()

    @property
    def request_id(self) -> object:
        return self._request.request_id

    def mark_resolved(self, decision: ApprovalDecision) -> None:
        """결정이 내려진 뒤 버튼을 걷고 결과만 남긴다.

        사람이 눌렀든 타임아웃으로 자동 처리됐든 같은 경로로 들어온다.
        """
        if self._resolved:
            return
        self._resolved = True
        self._ticker.stop()
        self._countdown.clear()
        _clear_layout(self._buttons)
        self._outcome.setText(_DECISION_LABELS.get(decision, decision.value))
        self._outcome.show()

    def _decide(self, decision: ApprovalDecision) -> None:
        if self._resolved:
            return
        self.decided.emit(self._request.request_id, decision)

    def _update_countdown(self) -> None:
        remaining = int(self._deadline - time.time())
        if remaining <= 0:
            self._countdown.setText("Timed out")
            self._ticker.stop()
            return
        self._countdown.setText("Auto-resolves in {0}s".format(remaining))


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
