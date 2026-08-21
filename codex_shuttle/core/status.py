"""환경 체크 항목이 공통으로 쓰는 상태 값."""

from enum import Enum


class CheckStatus(Enum):
    """체크 카드 하나가 가질 수 있는 상태.

    UNKNOWN 은 아직 검사하지 않은 상태고, CHECKING 은 검사가 진행 중인 상태다.
    나머지 셋은 검사가 끝난 뒤의 판정 결과다.
    """

    UNKNOWN = "unknown"
    CHECKING = "checking"
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"

    @property
    def is_settled(self) -> bool:
        """검사가 끝나 판정이 확정된 상태인지."""
        return self in (CheckStatus.OK, CheckStatus.WARNING, CheckStatus.ERROR)
