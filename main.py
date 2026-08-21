"""CodexShuttle 실행 진입점.

PyCharm 실행 구성이 이 파일을 가리키는 경우가 많아 런처로 남겨 둔다.
터미널에서는 `python -m codex_shuttle` 로도 같은 결과를 얻는다.
"""

import sys

from codex_shuttle.app import run

if __name__ == "__main__":
    sys.exit(run())
