"""환경 탭 — Codex CLI · 로그인 · 사용 한도 · 모델 카드."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from codex_shuttle.core.account import AccountInfo, ModelsInfo, RateLimitInfo
from codex_shuttle.core.codex_cli import CodexCliInfo
from codex_shuttle.ui.model_card import ModelCard
from codex_shuttle.ui.status_card import StatusCard
from codex_shuttle.ui.usage_card import UsageCard


class EnvironmentPanel(QScrollArea):
    """환경 점검 카드를 세로로 쌓아 보여 준다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._cli_card = StatusCard("Codex CLI")
        self.refresh_button: QPushButton = self._cli_card.add_action("Recheck")
        self.refresh_button.setToolTip("F5 · Re-run every check")

        self._account_card = StatusCard("Sign-in")
        self._usage_card = UsageCard()
        self._model_card = ModelCard()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        for card in (
            self._cli_card,
            self._account_card,
            self._usage_card,
            self._model_card,
        ):
            layout.addWidget(card)
        layout.addStretch(1)
        self.setWidget(container)

    def apply_cli(self, info: CodexCliInfo) -> None:
        self._cli_card.set_status(info.status, info.headline)
        self._cli_card.set_rows(
            [("Path", info.executable)] if info.executable else []
        )
        self._cli_card.set_detail(info.detail)

    def apply_account(self, info: AccountInfo) -> None:
        self._account_card.set_status(info.status, info.headline)
        self._account_card.set_rows(info.rows)
        self._account_card.set_detail(info.detail)

    def apply_rate_limits(self, info: RateLimitInfo) -> None:
        self._usage_card.apply(info)

    def apply_models(self, info: ModelsInfo) -> None:
        self._model_card.apply(info)
