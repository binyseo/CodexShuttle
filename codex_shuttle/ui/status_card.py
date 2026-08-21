"""환경 점검 항목 하나를 보여 주는 카드 위젯."""

from collections.abc import Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from codex_shuttle.core.status import CheckStatus
from codex_shuttle.ui import theme

_DOT_SIZE = 10
_DETAIL_MIN_HEIGHT = 44
_DETAIL_MAX_HEIGHT = 140


class StatusCard(QFrame):
    """상태 점 + 제목 + 헤드라인 + 키/값 행 + 접이식 상세로 이루어진 카드.

    CLI 체크뿐 아니라 뒤이어 붙을 로그인·모델·사용량 카드도 같은 위젯을 쓴다.
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusCard")
        self.setStyleSheet(theme.CARD_STYLE)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self._status = CheckStatus.UNKNOWN

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        root.addLayout(self._build_header(title))

        self._headline = QLabel()
        self._headline.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self._headline)

        # 하위 카드가 게이지나 목록을 끼워 넣는 자리.
        self._extra = QVBoxLayout()
        self._extra.setContentsMargins(0, 4, 0, 0)
        self._extra.setSpacing(6)
        root.addLayout(self._extra)

        self._rows = QGridLayout()
        self._rows.setContentsMargins(0, 2, 0, 0)
        self._rows.setHorizontalSpacing(12)
        self._rows.setVerticalSpacing(4)
        self._rows.setColumnStretch(1, 1)
        root.addLayout(self._rows)

        root.addLayout(self._build_detail())

        self._apply_status_style()

    def _build_header(self, title: str) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(8)

        self._dot = QLabel()
        self._dot.setFixedSize(_DOT_SIZE, _DOT_SIZE)
        header.addWidget(self._dot)

        self._title = QLabel(title)
        self._title.setObjectName("CardTitle")
        header.addWidget(self._title)

        header.addStretch(1)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(6)
        header.addLayout(self._actions)
        return header

    def _build_detail(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)

        self._detail_toggle = QToolButton()
        self._detail_toggle.setObjectName("DetailToggle")
        self._detail_toggle.setText("Details")
        self._detail_toggle.setCheckable(True)
        self._detail_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._detail_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._detail_toggle.toggled.connect(self._on_detail_toggled)
        self._detail_toggle.hide()
        box.addWidget(self._detail_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self._detail_view = QPlainTextEdit()
        self._detail_view.setObjectName("DetailView")
        self._detail_view.setReadOnly(True)
        self._detail_view.setFixedHeight(_DETAIL_MIN_HEIGHT)
        self._detail_view.hide()
        box.addWidget(self._detail_view)
        return box

    def add_action(self, text: str) -> QPushButton:
        """카드 우측 상단에 버튼을 추가하고 그 버튼을 돌려준다."""
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._actions.addWidget(button)
        return button

    def set_status(self, status: CheckStatus, headline: str) -> None:
        """상태 점 색과 헤드라인 문구를 갱신한다."""
        self._status = status
        self._headline.setText(headline)
        self._apply_status_style()

    def set_rows(self, rows: Sequence[tuple[str, str]]) -> None:
        """키/값 행을 통째로 교체한다."""
        self._clear_rows()
        muted = theme.muted_color(self.palette())
        for index, (label, value) in enumerate(rows):
            key = QLabel(label)
            key.setStyleSheet("color: " + muted + ";")
            key.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
            )

            text = QLabel(value)
            text.setWordWrap(True)
            text.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )

            self._rows.addWidget(key, index, 0)
            self._rows.addWidget(text, index, 1)

    def set_detail(self, text: str) -> None:
        """상세 영역 내용을 설정한다. 빈 문자열이면 토글 자체를 감춘다."""
        content = text.strip()
        self._detail_view.setPlainText(content)
        self._detail_view.setFixedHeight(self._detail_height(content))
        has_content = bool(content)
        self._detail_toggle.setVisible(has_content)
        if not has_content:
            self._detail_toggle.setChecked(False)
            self._detail_view.hide()

    def add_content(self, widget: QWidget) -> None:
        """헤드라인과 키/값 행 사이에 위젯을 끼워 넣는다."""
        self._extra.addWidget(widget)

    def _detail_height(self, content: str) -> int:
        """상세 박스 높이를 줄 수에 맞춘다.

        줄바꿈만 세므로 자동 줄바꿈된 긴 줄은 반영되지 않는다. 그 경우 상한에 걸려
        스크롤이 생기는 정도라 실사용에 문제가 없다.
        """
        lines = content.count("\n") + 1 if content else 1
        spacing = self._detail_view.fontMetrics().lineSpacing()
        return max(_DETAIL_MIN_HEIGHT, min(_DETAIL_MAX_HEIGHT, lines * spacing + 16))

    def _on_detail_toggled(self, checked: bool) -> None:
        self._detail_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self._detail_view.setVisible(checked)

    def _clear_rows(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # deleteLater만 하면 이벤트 루프가 돌기 전까지 화면에 남는다.
                widget.setParent(None)
                widget.deleteLater()

    def _apply_status_style(self) -> None:
        color = theme.status_color(self._status, self.palette())
        self._dot.setStyleSheet(
            "background-color: {0}; border-radius: {1}px;".format(
                color, _DOT_SIZE // 2
            )
        )
        self._headline.setStyleSheet("color: " + color + "; font-size: 14px;")
        self._dot.setToolTip(theme.STATUS_TEXT[self._status])

    def changeEvent(self, event) -> None:
        """시스템 테마가 바뀌면 팔레트에서 색을 다시 계산한다."""
        super().changeEvent(event)
        if event.type() == event.Type.PaletteChange:
            self._apply_status_style()
