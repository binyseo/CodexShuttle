"""app-server 응답을 화면에 쓸 형태로 옮긴다."""

from dataclasses import dataclass
from datetime import datetime

from codex_shuttle.core.status import CheckStatus

# 잔여율 임계치. 남은 양이 이 아래로 떨어지면 게이지와 상태 점 색이 바뀐다.
REMAINING_WARNING_PERCENT = 20
REMAINING_CRITICAL_PERCENT = 5

_AUTH_LABELS = {
    "chatgpt": "ChatGPT",
    "apiKey": "API key",
    "amazonBedrock": "Amazon Bedrock",
}

_PLAN_LABELS = {
    "free": "Free",
    "go": "Go",
    "plus": "Plus",
    "pro": "Pro",
    "prolite": "Pro Lite",
    "team": "Team",
    "business": "Business",
    "enterprise": "Enterprise",
    "edu": "Edu",
    "unknown": "Unknown",
}


def _label(table: dict[str, str], key: str | None) -> str | None:
    if not key:
        return None
    return table.get(key, key)


def _format_window(minutes: int | None) -> str:
    """창 길이를 사람이 읽는 단위로. 10080분이면 '7일'."""
    if not minutes:
        return "unknown window"
    if minutes % 1440 == 0:
        return "{0}d".format(minutes // 1440)
    if minutes % 60 == 0:
        return "{0}h".format(minutes // 60)
    return "{0}m".format(minutes)


def _format_remaining(seconds: int) -> str:
    if seconds <= 0:
        return "soon"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "in {0}d {1}h".format(days, hours)
    if hours:
        return "in {0}h {1}m".format(hours, minutes)
    return "in {0}m".format(max(minutes, 1))


@dataclass(slots=True)
class RateLimitWindow:
    """사용 한도 창 하나(주 한도 또는 보조 한도)."""

    used_percent: int
    window_minutes: int | None = None
    resets_at: int | None = None

    @property
    def remaining_percent(self) -> int:
        """남은 한도. API는 사용률만 주므로 여기서 뒤집는다."""
        return max(0, min(100, 100 - self.used_percent))

    @property
    def status(self) -> CheckStatus:
        if self.remaining_percent <= REMAINING_CRITICAL_PERCENT:
            return CheckStatus.ERROR
        if self.remaining_percent <= REMAINING_WARNING_PERCENT:
            return CheckStatus.WARNING
        return CheckStatus.OK

    @property
    def caption(self) -> str:
        """'7일 창 · 08-20 14:53 재설정 (3시간 12분 뒤)' 형태의 부연."""
        parts = [_format_window(self.window_minutes) + " window"]
        if self.resets_at:
            moment = datetime.fromtimestamp(self.resets_at)
            remaining = int(self.resets_at - datetime.now().timestamp())
            parts.append(
                "resets {0} ({1})".format(
                    moment.strftime("%m-%d %H:%M"), _format_remaining(remaining)
                )
            )
        return " · ".join(parts)


@dataclass(slots=True)
class AccountInfo:
    """로그인 카드에 표시할 내용."""

    status: CheckStatus = CheckStatus.UNKNOWN
    headline: str = "Not checked"
    auth_type: str | None = None
    email: str | None = None
    plan: str | None = None
    detail: str = ""

    @property
    def rows(self) -> list[tuple[str, str]]:
        # 플랜은 헤드라인("ChatGPT · Plus")에 이미 들어 있어 행으로 반복하지 않는다.
        return [("Account", self.email)] if self.email else []


@dataclass(slots=True)
class RateLimitInfo:
    """사용량 카드에 표시할 내용."""

    status: CheckStatus = CheckStatus.UNKNOWN
    headline: str = "Not checked"
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    plan: str | None = None
    credit_balance: str | None = None
    unlimited: bool = False
    reset_credits: int = 0
    # 백엔드가 한도 도달을 알려 준 경우. 잡을 새로 던져도 막힌다.
    limit_reached: bool = False
    detail: str = ""

    @property
    def remaining_percent(self) -> int | None:
        return self.primary.remaining_percent if self.primary else None

    @property
    def is_exhausted(self) -> bool:
        """지금 잡을 던지면 막힐 상태인지."""
        if self.limit_reached:
            return True
        remaining = self.remaining_percent
        return remaining is not None and remaining <= 0

    @property
    def rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if self.unlimited:
            rows.append(("Credits", "Unlimited"))
        elif self.credit_balance is not None:
            rows.append(("Credits", self.credit_balance))
        if self.reset_credits:
            rows.append(("Reset credits", "{0}".format(self.reset_credits)))
        return rows


@dataclass(slots=True)
class ModelInfo:
    """모델 목록의 항목 하나."""

    slug: str
    display_name: str
    efforts: tuple[str, ...] = ()
    default_effort: str | None = None
    is_default: bool = False
    description: str = ""


@dataclass(slots=True)
class ModelsInfo:
    """모델 카드에 표시할 내용."""

    status: CheckStatus = CheckStatus.UNKNOWN
    headline: str = "Not checked"
    models: tuple[ModelInfo, ...] = ()
    detail: str = ""


def parse_account(result: dict) -> AccountInfo:
    """account/read 응답을 옮긴다. account가 없으면 미로그인으로 본다."""
    account = result.get("account")
    if not isinstance(account, dict):
        return AccountInfo(
            status=CheckStatus.ERROR,
            headline="Not signed in",
            detail="Run codex login in a terminal to sign in.",
        )

    auth_type = account.get("type")
    auth_label = _label(_AUTH_LABELS, auth_type) or "Unknown auth"
    plan = _label(_PLAN_LABELS, account.get("planType"))
    headline = auth_label if plan is None else "{0} · {1}".format(auth_label, plan)

    return AccountInfo(
        status=CheckStatus.OK,
        headline=headline,
        auth_type=auth_type,
        email=account.get("email"),
        plan=plan,
    )


def parse_rate_limits(payload: dict) -> RateLimitInfo:
    """account/rateLimits/read 응답과 account/rateLimits/updated 알림을 함께 처리한다.

    두 페이로드 모두 rateLimits 키에 같은 스냅샷을 담고 있어 파서를 나누지 않는다.
    """
    snapshot = payload.get("rateLimits")
    if not isinstance(snapshot, dict):
        return RateLimitInfo(
            status=CheckStatus.WARNING,
            headline="No usage data",
            detail="The response has no rateLimits.",
        )

    primary = _parse_window(snapshot.get("primary"))
    secondary = _parse_window(snapshot.get("secondary"))
    credits = snapshot.get("credits") if isinstance(snapshot.get("credits"), dict) else {}
    reset_credits = snapshot.get("rateLimitResetCredits")
    if not isinstance(reset_credits, dict):
        reset_credits = payload.get("rateLimitResetCredits")
    available = 0
    if isinstance(reset_credits, dict):
        available = int(reset_credits.get("availableCount") or 0)

    if primary is None:
        status = CheckStatus.WARNING
        headline = "Could not read usage"
    else:
        status = primary.status
        headline = "{0}% left".format(primary.remaining_percent)

    reached = snapshot.get("rateLimitReachedType")
    if reached:
        status = CheckStatus.ERROR
        headline = "Limit reached"

    return RateLimitInfo(
        status=status,
        headline=headline,
        primary=primary,
        secondary=secondary,
        plan=_label(_PLAN_LABELS, snapshot.get("planType")),
        credit_balance=credits.get("balance"),
        unlimited=bool(credits.get("unlimited")),
        reset_credits=available,
        limit_reached=bool(reached),
        detail=str(reached) if reached else "",
    )


def _parse_window(raw: object) -> RateLimitWindow | None:
    if not isinstance(raw, dict) or raw.get("usedPercent") is None:
        return None
    return RateLimitWindow(
        used_percent=int(raw["usedPercent"]),
        window_minutes=raw.get("windowDurationMins"),
        resets_at=raw.get("resetsAt"),
    )


def parse_models(result: dict) -> ModelsInfo:
    """model/list 응답을 옮긴다. 숨김 처리된 내부 모델은 제외한다."""
    data = result.get("data")
    if not isinstance(data, list):
        return ModelsInfo(
            status=CheckStatus.WARNING,
            headline="Could not read the list",
            detail="The response has no data array.",
        )

    models: list[ModelInfo] = []
    for entry in data:
        if not isinstance(entry, dict) or entry.get("hidden"):
            continue
        efforts = tuple(
            item.get("reasoningEffort")
            for item in entry.get("supportedReasoningEfforts") or []
            if isinstance(item, dict) and item.get("reasoningEffort")
        )
        slug = entry.get("id") or entry.get("model") or ""
        models.append(
            ModelInfo(
                slug=slug,
                display_name=entry.get("displayName") or slug,
                efforts=efforts,
                default_effort=entry.get("defaultReasoningEffort"),
                is_default=bool(entry.get("isDefault")),
                description=entry.get("description") or "",
            )
        )

    if not models:
        return ModelsInfo(status=CheckStatus.WARNING, headline="No models available")

    return ModelsInfo(
        status=CheckStatus.OK,
        headline="{0} available".format(len(models)),
        models=tuple(models),
    )
