"""사용 가능한 모델 목록을 보여 주는 카드."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QLabel, QWidget

from codex_shuttle.core.account import ModelsInfo
from codex_shuttle.core.status import CheckStatus
from codex_shuttle.ui import theme
from codex_shuttle.ui.status_card import StatusCard


class ModelCard(StatusCard):
    """모델 이름 · 기본 여부 · 지원 추론 강도를 한 줄씩 나열한다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Available models", parent)

        self._list = QWidget()
        self._grid = QGridLayout(self._list)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(4)
        self._grid.setColumnStretch(2, 1)
        self.add_content(self._list)
        self._list.hide()

    def apply(self, info: ModelsInfo) -> None:
        self.set_status(info.status, info.headline)
        self.set_detail(info.detail)
        self._clear()

        if not info.models:
            self._list.hide()
            return

        palette = self.palette()
        muted = theme.muted_color(palette)
        accent = theme.status_color(CheckStatus.OK, palette)

        for row, model in enumerate(info.models):
            name = QLabel(model.display_name)
            name.setToolTip(model.description or model.slug)
            self._grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignTop)

            badge = QLabel("default" if model.is_default else "")
            badge.setStyleSheet("color: " + accent + "; font-size: 11px;")
            self._grid.addWidget(badge, row, 1, Qt.AlignmentFlag.AlignTop)

            efforts = QLabel(" · ".join(model.efforts))
            efforts.setWordWrap(True)
            efforts.setStyleSheet("color: " + muted + "; font-size: 11px;")
            self._grid.addWidget(efforts, row, 2, Qt.AlignmentFlag.AlignTop)

        self._list.show()

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # deleteLater만 하면 이벤트 루프가 돌기 전까지 화면에 남는다.
                widget.setParent(None)
                widget.deleteLater()
