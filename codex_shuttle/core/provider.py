"""지금 codex가 어느 모델 provider로 도는지 판별한다.

ChatGPT 로그인과 사용 한도는 codex 기본 provider(openai)에만 해당한다. 올라마 같은
로컬 provider만 연결해 쓰는 사용자는 로그인하지 않아도 codex가 정상으로 돌기 때문에,
그 상태를 오류로 잡으면 잡을 아예 던질 수 없게 된다. 그래서 provider를 먼저 확인하고
로그인·한도 검사를 적용할지 정한다.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

# model_provider를 적지 않았을 때 codex가 쓰는 기본값.
DEFAULT_PROVIDER = "openai"

_CONFIG_FILE = "config.toml"


@dataclass(slots=True)
class ProviderInfo:
    """활성 provider와, 그 provider가 ChatGPT 인증을 쓰는지 여부.

    확인에 실패하면 기본값 그대로 둔다. 즉 ChatGPT 인증을 쓰는 것으로 보고 로그인
    검사를 그대로 적용한다. provider를 모르는 채로 검사를 건너뛰면 정작 로그인이
    필요한 사용자가 잡을 던진 뒤에야 실패를 보게 된다.
    """

    name: str | None = None
    uses_chatgpt_auth: bool = True
    # app-server · config · default 중 하나. 어디서 알아냈는지 상세 표시에 쓴다.
    source: str = "default"

    @property
    def display_name(self) -> str:
        return self.name or DEFAULT_PROVIDER


def resolve_provider(result: dict | None) -> ProviderInfo:
    """config/read 응답에서 provider를 읽는다. 응답이 없으면 설정 파일로 넘어간다.

    config/read는 프로필과 프로젝트 레이어까지 반영된 값을 주므로 이쪽이 정확하다.
    구버전 CLI에는 이 메서드가 없어서 파일 파싱을 남겨 둔다.
    """
    config = (result or {}).get("config")
    if isinstance(config, dict):
        return _from_config(config, source="app-server")
    return read_provider_from_file()


def read_provider_from_file() -> ProviderInfo:
    """$CODEX_HOME/config.toml 에서 provider를 읽는다.

    config/read가 없는 구버전 CLI용 대비책이다. 파일이 없거나 읽히지 않거나
    model_provider가 적혀 있지 않으면 기본값(openai, 로그인 필요)으로 둔다.
    """
    path = _codex_home() / _CONFIG_FILE
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ProviderInfo()
    if not isinstance(config, dict):
        return ProviderInfo()
    return _from_config(_with_profile(config), source="config")


def _codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override)
    return Path.home() / ".codex"


def _with_profile(config: dict) -> dict:
    """활성 프로필이 model_provider를 덮어썼으면 그 값을 위로 올린다.

    config/read는 이 병합을 이미 마친 값을 주지만 파일을 직접 읽을 때는 우리가 해야 한다.
    """
    name = os.environ.get("CODEX_PROFILE") or config.get("profile")
    if not isinstance(name, str) or not name:
        return config
    profiles = config.get("profiles")
    profile = profiles.get(name) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict) or "model_provider" not in profile:
        return config
    merged = dict(config)
    merged["model_provider"] = profile["model_provider"]
    return merged


def _from_config(config: dict, *, source: str) -> ProviderInfo:
    name = config.get("model_provider")
    if not isinstance(name, str) or not name:
        return ProviderInfo(source=source)
    if name == DEFAULT_PROVIDER:
        return ProviderInfo(name=name, source=source)
    return ProviderInfo(
        name=name,
        uses_chatgpt_auth=_requires_openai_auth(config, name),
        source=source,
    )


def _requires_openai_auth(config: dict, name: str) -> bool:
    """직접 정의한 provider가 ChatGPT 인증을 그대로 쓰는 경우를 걸러 낸다.

    model_providers 항목에 requires_openai_auth = true 를 적어 두면 이름만 다를 뿐
    ChatGPT 계정으로 붙는다. 이 경우에는 로그인 검사를 계속 적용해야 한다.
    """
    providers = config.get("model_providers")
    entry = providers.get(name) if isinstance(providers, dict) else None
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("requires_openai_auth"))
