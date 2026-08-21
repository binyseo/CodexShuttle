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
from codex_shuttle.core.provider import DEFAULT_PROVIDER, ProviderInfo
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
        # CLI 카드와 provider 정보를 함께 들고 있어야 어느 쪽이 먼저 도착해도
        # 행을 온전히 다시 그릴 수 있다.
        self._cli_info: CodexCliInfo | None = None
        self._provider = ProviderInfo()
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
        self._cli_info = info
        self._cli_card.set_status(info.status, info.headline)
        self._cli_card.set_rows(self._cli_rows())
        self._cli_card.set_detail(info.detail)

    def apply_provider(self, info: ProviderInfo) -> None:
        """provider에 따라 로그인 카드를 감추고, provider 이름을 CLI 카드에 남긴다.

        ChatGPT를 거치지 않는 provider에서는 로그인 카드가 늘 '로그인 안 됨'으로
        남아 조치할 것이 있는 것처럼 보인다. 해당 없는 카드라 아예 감춘다. 대신
        어느 provider로 도는지는 항상 보이는 CLI 카드에 적어 둔다.
        """
        self._provider = info
        self._account_card.setVisible(info.uses_chatgpt_auth)
        self._cli_card.set_rows(self._cli_rows())

    def _cli_rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        info = self._cli_info
        if info is not None and info.executable:
            rows.append(("Path", info.executable))
        # 기본 provider면 적지 않는다. 대부분의 사용자에게는 없는 것과 같다.
        if self._provider.name and self._provider.name != DEFAULT_PROVIDER:
            rows.append(("Provider", self._provider.display_name))
        return rows

    def apply_account(self, info: AccountInfo) -> None:
        self._account_card.set_status(info.status, info.headline)
        self._account_card.set_rows(info.rows)
        self._account_card.set_detail(info.detail)

    def apply_rate_limits(self, info: RateLimitInfo) -> None:
        self._usage_card.apply(info)

    def apply_models(self, info: ModelsInfo) -> None:
        self._model_card.apply(info)
