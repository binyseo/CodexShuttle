# CodexShuttle

한국어 | [English](README.en.md)

**클로드가 Codex를 서브 에이전트로 활용가능 하게 해 주는 도구입니다.**

클로드 세션에서 Codex에게 잡을 할당하고, 처리 결과를 다시 클로드 세션에서 받아
이어서 작업합니다. 설치 후 사용자가 할 일은 없습니다 — 앱을 띄우는 것도, 잡을
던지고 결과를 회수하는 것도 전부 클로드가 알아서 합니다. 사용자는 클로드에게
"이 작업은 Codex한테 시켜줘"라고 말하기만 하면 됩니다.

```
클로드 세션 ──▶ CodexShuttle GUI ──▶ 클로드 세션
  (작업 위임)      (실제 작업 수행)      (결과 확인)
```

잡 하나마다 새 Codex 세션이 열려 독립적으로 처리됩니다. GUI 앱이 초기화될 때
사용 가능한 모델과 모델별 추론 강도 목록이 클로드에게 전달되며, 이를 바탕으로
잡마다 어떤 모델을 어떤 추론 강도로 돌릴지 클로드를 통해 지정할 수 있습니다.

각 잡은 독립된 세션이라 클로드 세션의 대화 내용이나 앞선 잡의 맥락을 전혀
모릅니다. 그래서 작업 지시는 md 파일로 전달하며, 대상 범위·제약·배경 등 작업에
필요한 컨텍스트를 이 파일에 충분히 담아야 원하는 결과를 얻을 수 있습니다.

클로드가 잡을 부여하면 진행 과정을 지켜볼 수 있는 GUI 앱이 자동으로 뜹니다.
명령 실행·추론·파일 변경 같은 중간 과정은 이 창에서만 보이고 클로드에게는
전달되지 않아, 불필요한 토큰 소모 없이 최종 결과만 오갑니다. 단, GUI 앱을 강제
종료하면 수행 중인 잡의 결과를 클로드가 받을 수 없으니 유의해 주시기 바랍니다.

## 요구 사항

| | |
|---|---|
| uv | [설치 안내](https://docs.astral.sh/uv/getting-started/installation/) — `winget install astral-sh.uv` (macOS: `brew install uv`) |
| Codex CLI | `npm install -g @openai/codex` 후 `codex login`. 올라마 같은 로컬 모델 provider를 붙여 쓴다면 로그인은 필요 없습니다 |
| OS | macOS · Windows (Qt 로컬 소켓 사용) |

Python은 따로 설치할 필요가 없습니다. uv가 필요한 버전을 알아서 받아 씁니다.

## 설치

```bash
uv tool install git+https://github.com/binyseo/CodexShuttle
```

clone도 가상환경도 필요 없습니다. 이 한 줄이 격리된 환경을 만들고
`codex-shuttle` 명령을 PATH에 등록합니다. 업데이트는
`uv tool upgrade codex-shuttle` 로 합니다.

### 스킬 배치

클로드에게 이 도구의 사용법을 알려 주는 `SKILL.md`는 패키지 안에 포함되어
있으며, 아래 명령 한 줄로 설치가 끝납니다.

```bash
codex-shuttle install-skill --user            # ~/.claude/skills/ — 모든 프로젝트에서
codex-shuttle install-skill --project         # 현재 폴더의 .claude/skills/ 에만
codex-shuttle install-skill --project ~/proj  # 특정 폴더에
```

여기까지가 사용자가 할 일의 전부입니다. 다음 클로드 세션부터는 스킬을 읽은
클로드가 앱 실행부터 잡 회수까지 알아서 처리합니다.

### 업데이트할 때는 스킬도 다시 깔아야 합니다

`install-skill`은 패키지 안의 `SKILL.md`를 클로드가 읽는 자리로 **복사**합니다.
그래서 `uv tool upgrade`로 패키지를 올려도 이미 배치된 스킬 파일은 예전 내용
그대로 남습니다. 업그레이드 뒤에 같은 명령을 한 번 더 실행하세요.

```bash
uv tool upgrade codex-shuttle
codex-shuttle install-skill --user
```

설치본 끝에는 어느 버전에서 나온 사본인지 표식이 붙습니다. 그 표식이 낡았으면
**아무것도 묻지 않고 새 내용으로 덮어씁니다.** 이미 최신이면 `Already up to date`
만 출력하고 파일에 손대지 않습니다.

`--force`가 필요한 경우는 하나뿐입니다 — 배치된 파일을 직접 고쳐 뒀을 때. 이때는
사용자가 고친 내용이 날아가지 않도록 덮어쓰기를 멈추고 알려 줍니다.

`--project`로도 배치해 뒀다면 그 폴더마다 같은 명령을 한 번씩 실행합니다. 이미
열려 있는 클로드 세션은 재시작해야 새 스킬을 읽습니다.

## GUI 화면 안내

### 환경 탭

Codex CLI 설치 상태, 계정 로그인, 사용 한도, 사용 가능한 모델을 한눈에 보여
줍니다. 클로드도 잡을 던지기 전에 같은 정보를 확인하고, 문제가 있으면 스스로
멈추고 사용자에게 알립니다.

| 카드 | 정상일 때 |
|---|---|
| Codex CLI | 초록 점 + 버전 |
| 로그인 | `ChatGPT · Plus` 등 |
| 사용 한도 | 남은 비율과 재설정 시각 |
| 사용 가능한 모델 | 목록 |

하나라도 빨간색이면 그 카드의 안내대로 조치합니다. `F5`로 다시 검사할 수 있습니다.

`config.toml`의 `model_provider`가 올라마 같은 로컬·외부 provider로 잡혀 있으면
ChatGPT 로그인과 사용 한도가 해당하지 않습니다. 이때는 로그인 카드가 사라지고
사용 한도는 회색 `Not used by ...`로 바뀌며, provider 이름이 Codex CLI 카드에
표시됩니다. 로그인하지 않았다는 이유로 잡이 막히지도 않습니다.

### 작업 탭

클로드가 배정한 잡 목록과 각 잡의 대화 상세를 확인할 수 있습니다.

- 잡마다 `클로드 세션 · 상태 · 경과`가 붙습니다. 세션 자리에는 `client-id`가, 없으면 `Claude`가 들어갑니다
- 승인 대기가 생기면 그 잡의 행이 깜빡입니다. 다른 탭에 있으면 **작업** 탭 라벨이 깜빡입니다
- **중단** 으로 진행 중인 턴을 멈출 수 있습니다
- **내역 저장** 으로 대화 전문을 Markdown이나 JSON으로 뽑습니다. 승인 이력도 함께 들어갑니다
- 끝난 잡은 우클릭 → **지우기**, 또는 하단 **완료된 잡 지우기**로 삭제합니다

## 동작 규칙

| 항목 | 값 |
|---|---|
| 동시 실행 | 5개까지. 넘으면 대기했다가 순서대로 실행합니다 |
| 잡 보관 | 끝난 잡 최근 100건 |
| 승인 대기 기본 | 300초. 넘으면 잡에 지정된 동작으로 자동 처리합니다 |
| 잡 수명 | 잡 하나가 세션 하나입니다. 끝나면 `thread/unsubscribe`로 Codex 스레드를 놓아줍니다 |
| 보고서 회수 | 2,000자까지 stdout JSON에 싣고, 넘으면 파일로 빼서 `result_path`로 알립니다 |
| 오류 상한 | 2,000자까지 stdout에 싣고, 넘으면 파일로 빼서 `error_path`로 알립니다 |

## 구조

```
codex_shuttle/
  core/
    codex_cli.py     Codex CLI 설치·버전 확인
    app_server.py    codex app-server(stdio JSON-RPC) 연결
    account.py       계정·사용 한도·모델 목록 파싱
    environment.py   위 셋을 묶어 환경 상태로
    job.py           잡·대화 항목·승인 요청 모델
    job_runner.py    잡 실행, 스트리밍 반영, 승인 처리
    ipc.py           CLI와의 로컬 소켓 창구
    transcript.py    대화 내역 파일로 내보내기
  ui/                PyQt6 위젯과 창
  client/            codex-shuttle 명령
  skill/SKILL.md     클로드에게 배포하는 스킬 원본 (install-skill 이 복사합니다)
```

## 그 외

클로드는 작업 파일과 보고서를 `<프로젝트>/.codex-shuttle/` 에 씁니다. 저장소에
남지 않도록 `.gitignore` 에 한 줄 넣어 두시기 바랍니다.

```
.codex-shuttle/
```
