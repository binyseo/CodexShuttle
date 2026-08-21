"""잡 하나의 대화를 항목별 위젯으로 쌓아 보여 준다."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from codex_shuttle.core.job import JobItem
from codex_shuttle.ui import theme

# 항목 타입 -> (표시 이름, 고정폭 본문 여부, 처음부터 접어 둘지)
_ITEM_STYLES = {
    "userMessage": ("User", False, False),
    "agentMessage": ("Codex", False, False),
    "plan": ("Plan", False, False),
    "reasoning": ("Reasoning", False, True),
    # 명령 출력은 길고 잡음이 많아 접어 둔다. 머리말에 명령과 종료 코드가 보이므로
    # 필요할 때만 펼치면 된다.
    "commandExecution": ("Command", True, True),
    "fileChange": ("File change", True, False),
    "mcpToolCall": ("MCP tool", True, True),
    "dynamicToolCall": ("Tool call", True, True),
    "webSearch": ("Web search", False, True),
    "contextCompaction": ("Context compaction", False, True),
    "subAgentActivity": ("Subagent", False, True),
}

_BODY_MIN_HEIGHT = 40
_BODY_MAX_HEIGHT = 260
# 바닥에서 이만큼 안쪽이면 아직 바닥을 보고 있는 것으로 친다.
_FOLLOW_SLACK = 24


def _should_show(item: JobItem) -> bool:
    """화면에 그릴 항목인지.

    codex는 추론 항목을 만들어 놓고 요약을 내지 않는 경우가 있다. 그런 항목은
    제목만 남아 자리만 차지하므로 숨긴다. 나중에 내용이 채워지면 다시 보인다.
    """
    if item.item_type == "reasoning":
        return bool(item.text.strip())
    return True


class ItemWidget(QFrame):
    """대화 항목 하나. 타입에 따라 머리말과 본문 모양이 달라진다."""

    def __init__(self, item: JobItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        label, monospace, collapsed = _ITEM_STYLES.get(
            item.item_type, (item.item_type, False, True)
        )
        self._monospace = monospace
        # 명령 출력만 꼬리를 따라간다. 오류가 대개 끝에 나오기 때문이다. 파일 목록
        # 같은 나머지 고정폭 본문은 처음부터 보여야 읽힌다.
        self._follow_tail = item.item_type == "commandExecution"
        muted = theme.muted_color(self.palette())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(6)

        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(not collapsed)
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow
        )
        self._toggle.setStyleSheet("QToolButton { border: none; }")
        self._toggle.toggled.connect(self._on_toggled)
        header.addWidget(self._toggle)

        self._label = QLabel(label)
        self._label.setStyleSheet("color: {0}; font-size: 11px;".format(muted))
        header.addWidget(self._label)

        self._badge = QLabel()
        self._badge.setStyleSheet("color: {0}; font-size: 11px;".format(muted))
        header.addWidget(self._badge)
        header.addStretch(1)
        layout.addLayout(header)

        self._body = self._build_body(monospace)
        self._body.setVisible(not collapsed)
        layout.addWidget(self._body)

        self.update_item(item)

    def _build_body(self, monospace: bool) -> QWidget:
        if monospace:
            body = QPlainTextEdit()
            body.setReadOnly(True)
            body.setFrameShape(QFrame.Shape.NoFrame)
            body.setFont(
                QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            )
            body.setStyleSheet(
                "QPlainTextEdit { background-color: palette(alternate-base);"
                " border-radius: 6px; padding: 6px; }"
            )
            return body

        body = QLabel()
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setContentsMargins(20, 0, 0, 0)
        return body

    def update_item(self, item: JobItem) -> None:
        """내용과 배지를 갱신한다. 스트리밍 중에도 계속 불린다."""
        text = item.text or _fallback_text(item)
        if self._monospace:
            if self._body.toPlainText() != text:
                at_bottom = _at_bottom(self._body)
                self._body.setPlainText(text)
                bar = self._body.verticalScrollBar()
                bar.setValue(bar.maximum() if self._follow_tail and at_bottom else 0)
            self._body.setFixedHeight(self._body_height(text))
        else:
            self._body.setText(text)

        self._badge.setText(_badge_text(item))

    def _body_height(self, text: str) -> int:
        lines = text.count("\n") + 1 if text else 1
        spacing = self._body.fontMetrics().lineSpacing()
        return max(_BODY_MIN_HEIGHT, min(_BODY_MAX_HEIGHT, lines * spacing + 26))

    def _on_toggled(self, checked: bool) -> None:
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self._body.setVisible(checked)


def _fallback_text(item: JobItem) -> str:
    """본문 필드를 모르는 항목 타입일 때 그래도 뭔가 보여 준다."""
    payload = item.payload
    if not payload:
        return "(no content)" if item.completed else "…"
    for key in ("command", "query", "path", "tool", "status"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return "(no content)" if item.completed else "…"


def _badge_text(item: JobItem) -> str:
    payload = item.payload
    if item.item_type == "commandExecution":
        # 접힌 상태가 기본이라, 무엇을 실행했는지 머리말만 봐도 알 수 있어야 한다.
        parts = []
        command = payload.get("command")
        if isinstance(command, str) and command:
            parts.append(command if len(command) <= 64 else command[:61] + "…")
        exit_code = payload.get("exitCode")
        if exit_code is not None:
            parts.append("exit {0}".format(exit_code))
        elif not item.completed:
            parts.append("…")
        return "  ·  ".join(parts)
    if item.item_type == "fileChange":
        changes = payload.get("changes")
        if isinstance(changes, list) and changes:
            return "{0} files".format(len(changes))
        status = payload.get("status")
        return str(status) if status else ""
    if not item.completed:
        return "…"
    return ""


def _at_bottom(view: QPlainTextEdit) -> bool:
    bar = view.verticalScrollBar()
    return bar.value() >= bar.maximum() - 4


class ConversationView(QScrollArea):
    """항목 위젯을 세로로 쌓고, 바닥에 있을 때만 자동으로 따라 내려간다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(self._container)

        self._widgets: dict[str, ItemWidget] = {}

        # 바닥에 있는 동안에는 새 내용을 따라간다. 사용자가 위로 올리면 그 자리를
        # 지키고, 다시 바닥으로 내려오면 따라가기가 살아난다.
        self._follow = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)

    def apply_item(self, item: JobItem) -> None:
        """항목을 갱신하거나 없으면 새로 추가한다."""
        widget = self._widgets.get(item.item_id)
        if widget is None:
            widget = ItemWidget(item)
            self._widgets[item.item_id] = widget
            self._layout.insertWidget(self._layout.count() - 1, widget)
        else:
            widget.update_item(item)
        widget.setVisible(_should_show(item))

    def add_approval(self, widget: QWidget, item_id: str) -> None:
        """승인 위젯을 해당 항목 바로 아래에 끼워 넣는다."""
        anchor = self._widgets.get(item_id)
        index = self._layout.count() - 1
        if anchor is not None:
            index = self._layout.indexOf(anchor) + 1
        self._layout.insertWidget(index, widget)
        # 승인은 놓치면 안 되므로 위를 보고 있었더라도 끌어내린다.
        self._follow = True

    def _on_value_changed(self, value: int) -> None:
        self._follow = value >= self.verticalScrollBar().maximum() - _FOLLOW_SLACK

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        """내용이 늘어나 스크롤 범위가 바뀌는 시점에 바닥으로 따라 내려간다.

        위젯을 넣은 직후에는 레이아웃이 아직 갱신되지 않아 maximum이 옛 값이다.
        그래서 삽입 시점이 아니라 이 신호에서 처리해야 실제 바닥에 닿는다.
        """
        if self._follow:
            self.verticalScrollBar().setValue(maximum)
