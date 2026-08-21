"""CLI 체크와 app-server 조회를 하나로 묶어 카드에 흘려보낸다."""

from PyQt6.QtCore import QObject, pyqtSignal

from codex_shuttle.core.account import (
    AccountInfo,
    ModelsInfo,
    RateLimitInfo,
    parse_account,
    parse_models,
    parse_rate_limits,
)
from codex_shuttle.core.app_server import (
    METHOD_ACCOUNT_READ,
    METHOD_MODEL_LIST,
    METHOD_RATE_LIMITS_READ,
    NOTIFY_ACCOUNT_UPDATED,
    NOTIFY_RATE_LIMITS_UPDATED,
    AppServerClient,
)
from codex_shuttle.core.codex_cli import CodexCliChecker, CodexCliInfo
from codex_shuttle.core.status import CheckStatus

_CLI_REQUIRED = "The Codex CLI has to be checked first."


class EnvironmentMonitor(QObject):
    """환경 점검 전체를 진행한다.

    CLI 확인이 먼저고, CLI가 정상일 때만 app-server를 띄워 계정·모델·사용량을
    읽는다. 그래서 하위 세 항목은 CLI 결과에 종속된다.
    """

    cliChanged = pyqtSignal(object)  # CodexCliInfo
    accountChanged = pyqtSignal(object)  # AccountInfo
    modelsChanged = pyqtSignal(object)  # ModelsInfo
    rateLimitsChanged = pyqtSignal(object)  # RateLimitInfo

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cli = CodexCliChecker(self)
        self._client = AppServerClient(self)

        # CLI가 작업 가능 여부를 물어볼 수 있도록 최근 결과를 들고 있는다.
        self._account = AccountInfo()
        self._models = ModelsInfo()
        self._rate_limits = RateLimitInfo()
        self.accountChanged.connect(self._remember_account)
        self.modelsChanged.connect(self._remember_models)
        self.rateLimitsChanged.connect(self._remember_rate_limits)

        self._cli.stateChanged.connect(self._on_cli_state)
        self._client.connected.connect(self._request_all)
        self._client.disconnected.connect(self._on_disconnected)
        self._client.notified.connect(self._on_notified)

    @property
    def client(self) -> AppServerClient:
        """같은 app-server 연결을 잡 실행 쪽에서도 쓰도록 노출한다."""
        return self._client

    def refresh(self) -> None:
        """전체 재검사. 이미 진행 중이면 무시한다."""
        if self._cli.is_running:
            return
        self._emit_dependents(CheckStatus.CHECKING, "Waiting…")
        self._cli.check()

    def _remember_account(self, info: AccountInfo) -> None:
        self._account = info

    def _remember_models(self, info: ModelsInfo) -> None:
        self._models = info

    def _remember_rate_limits(self, info: RateLimitInfo) -> None:
        self._rate_limits = info

    def snapshot(self) -> dict:
        """지금 codex 작업을 던져도 되는지에 대한 요약.

        CLI가 이걸 받아 사전 점검에 쓴다. blockers가 비어 있으면 작업 가능하다.
        """
        cli = self._cli.info
        account = self._account
        usage = self._rate_limits

        blockers: list[str] = []
        if not cli.is_available:
            blockers.append("The Codex CLI is not usable: " + cli.headline)
        if account.status is not CheckStatus.OK:
            blockers.append("Check the sign-in state: " + account.headline)
        if usage.is_exhausted:
            reset = usage.primary.caption if usage.primary else ""
            blockers.append("The usage limit is exhausted. " + reset)

        return {
            "ready": not blockers,
            "blockers": blockers,
            "cli": {
                "status": cli.status.value,
                "installed": cli.executable is not None,
                "version": cli.version,
                "executable": cli.executable,
            },
            "account": {
                "status": account.status.value,
                "logged_in": account.status is CheckStatus.OK,
                "auth_type": account.auth_type,
                "plan": account.plan,
                "email": account.email,
            },
            "usage": {
                "status": usage.status.value,
                "remaining_percent": usage.remaining_percent,
                "limit_reached": usage.limit_reached,
                "exhausted": usage.is_exhausted,
                "window": usage.primary.caption if usage.primary else "",
                "resets_at": usage.primary.resets_at if usage.primary else None,
                "credit_balance": usage.credit_balance,
            },
            # 슬러그만 주면 클로드가 강도를 짐작해야 한다. model/list에서 이미
            # 받아 둔 값이므로 그대로 흘려보낸다.
            "models": [
                {
                    "slug": model.slug,
                    "name": model.display_name,
                    "default": model.is_default,
                    "efforts": list(model.efforts),
                    "default_effort": model.default_effort,
                    # 클로드가 모델을 고르는 유일한 근거다. 슬러그는 codex 버전마다
                    # 바뀌므로 이름으로 짐작하게 두면 안 된다.
                    "description": model.description,
                }
                for model in self._models.models
            ],
            "app_server_connected": self._client.is_ready,
        }

    def mark_account_expired(self, message: str) -> None:
        """인증 만료를 로그인 카드에 즉시 반영한다.

        잡 실행 중 codex가 401을 받으면 JobRunner가 이 경로로 알려 준다. 다음 재검사를
        기다리지 않고 바로 화면에 드러내기 위한 것이다.
        """
        self.accountChanged.emit(
            AccountInfo(
                status=CheckStatus.ERROR, headline="Auth expired", detail=message
            )
        )

    def shutdown(self) -> None:
        """창을 닫을 때 app-server를 정리한다."""
        self._client.stop()

    def _on_cli_state(self, info: CodexCliInfo) -> None:
        self.cliChanged.emit(info)

        if info.status is CheckStatus.CHECKING:
            return

        if not info.is_available or info.executable is None:
            self._client.stop()
            self._emit_dependents(CheckStatus.ERROR, _CLI_REQUIRED)
            return

        if self._client.is_ready:
            self._request_all()
        else:
            # start()는 이미 떠 있으면 아무것도 하지 않는다. 준비되면 connected로 이어진다.
            self._client.start(info.executable)

    def _request_all(self) -> None:
        self._emit_dependents(CheckStatus.CHECKING, "Loading…")
        self._client.request(
            METHOD_ACCOUNT_READ,
            on_result=lambda result: self.accountChanged.emit(parse_account(result)),
            on_error=lambda message: self.accountChanged.emit(
                AccountInfo(
                    status=CheckStatus.ERROR, headline="Request failed", detail=message
                )
            ),
        )
        self._client.request(
            METHOD_MODEL_LIST,
            on_result=lambda result: self.modelsChanged.emit(parse_models(result)),
            on_error=lambda message: self.modelsChanged.emit(
                ModelsInfo(
                    status=CheckStatus.ERROR, headline="Request failed", detail=message
                )
            ),
        )
        self._client.request(
            METHOD_RATE_LIMITS_READ,
            on_result=lambda result: self.rateLimitsChanged.emit(
                parse_rate_limits(result)
            ),
            on_error=lambda message: self.rateLimitsChanged.emit(
                RateLimitInfo(
                    status=CheckStatus.ERROR, headline="Request failed", detail=message
                )
            ),
        )

    def _on_notified(self, method: str, params: dict) -> None:
        if method == NOTIFY_RATE_LIMITS_UPDATED:
            self.rateLimitsChanged.emit(parse_rate_limits(params))
        elif method == NOTIFY_ACCOUNT_UPDATED:
            # 알림에는 인증 방식과 플랜만 실려 있어서 전체를 다시 읽는다.
            self._client.request(
                METHOD_ACCOUNT_READ,
                on_result=lambda result: self.accountChanged.emit(parse_account(result)),
            )

    def _on_disconnected(self, reason: str) -> None:
        detail = self._client.stderr_tail
        self._emit_dependents(CheckStatus.ERROR, "Disconnected", reason if not detail else detail)

    def _emit_dependents(
        self, status: CheckStatus, headline: str, detail: str = ""
    ) -> None:
        self.accountChanged.emit(
            AccountInfo(status=status, headline=headline, detail=detail)
        )
        self.modelsChanged.emit(
            ModelsInfo(status=status, headline=headline, detail=detail)
        )
        self.rateLimitsChanged.emit(
            RateLimitInfo(status=status, headline=headline, detail=detail)
        )
