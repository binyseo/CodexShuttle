"""사용량과 토큰 리미트를 게이지로 보여 주는 카드."""

from PyQt6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from codex_shuttle.core.account import RateLimitInfo, RateLimitWindow
from codex_shuttle.ui import theme
from codex_shuttle.ui.status_card import StatusCard

_BAR_HEIGHT = 8


class UsageGauge(QWidget):
    """제목 + 막대 + 부연 한 줄로 이루어진 사용률 게이지."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        muted = theme.muted_color(self.palette())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._title = QLabel(title)
        self._title.setStyleSheet("color: " + muted + "; font-size: 11px;")
        layout.addWidget(self._title)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(_BAR_HEIGHT)
        layout.addWidget(self._bar)

        self._caption = QLabel()
        self._caption.setWordWrap(True)
        self._caption.setStyleSheet("color: " + muted + "; font-size: 11px;")
        layout.addWidget(self._caption)

    def apply(self, window: RateLimitWindow) -> None:
        """창 하나의 잔여량을 반영한다. 막대는 남은 만큼 차 있다가 줄어든다."""
        self._bar.setValue(window.remaining_percent)
        self._bar.setToolTip(
            "{0}% used · {1}% left".format(window.used_percent, window.remaining_percent)
        )
        radius = _BAR_HEIGHT // 2
        self._bar.setStyleSheet(
            "QProgressBar {{ border: none; border-radius: {radius}px;"
            " background-color: {track}; }}"
            "QProgressBar::chunk {{ border-radius: {radius}px;"
            " background-color: {color}; }}".format(
                radius=radius,
                track=theme.track_color(self.palette()),
                color=theme.status_color(window.status, self.palette()),
            )
        )
        self._caption.setText(window.caption)


class UsageCard(StatusCard):
    """기본 한도와 보조 한도를 함께 보여 준다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Usage limits", parent)
        self._primary = UsageGauge("Primary")
        self._secondary = UsageGauge("Secondary")
        self.add_content(self._primary)
        self.add_content(self._secondary)
        self._primary.hide()
        self._secondary.hide()

    def apply(self, info: RateLimitInfo) -> None:
        self.set_status(info.status, info.headline)

        # 플랜은 로그인 카드가 이미 보여 주므로 여기서는 크레딧만 남긴다.
        self.set_rows(info.rows)
        self.set_detail(info.detail)

        for gauge, window in (
            (self._primary, info.primary),
            (self._secondary, info.secondary),
        ):
            if window is None:
                gauge.hide()
                continue
            gauge.apply(window)
            gauge.show()
