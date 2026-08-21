"""codex-shuttle 명령.

클로드 세션은 보통 이것만 쓴다.

    codex-shuttle run --task task.md --label "결제 정리" --cwd ~/proj --wait

`--wait`을 주면 잡이 끝날 때까지 이 프로세스가 살아 있다가 결과를 stdout에 찍고
종료한다. 백그라운드로 띄워 두면 종료가 곧 완료 통지가 된다.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from importlib import resources
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from codex_shuttle import APP_NAME, __version__
from codex_shuttle.client.connection import (
    NO_TIMEOUT,
    Connection,
    ConnectionLostError,
    NotRunningError,
)

EXIT_OK = 0
EXIT_JOB_FAILED = 1
EXIT_JOB_INTERRUPTED = 2
EXIT_NOT_RUNNING = 3
EXIT_USAGE = 4
# codex 쪽이 작업을 받을 수 없는 상태(미설치·미로그인·한도 소진). 스킬이 이 값으로
# 잡을 던지기 전에 걸러 낼 수 있다.
EXIT_NOT_READY = 5

_STATE_EXIT = {
    "succeeded": EXIT_OK,
    "failed": EXIT_JOB_FAILED,
    "interrupted": EXIT_JOB_INTERRUPTED,
}

# 떼어 낸 프로세스로 GUI를 띄우기 위한 Windows 플래그.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_LAUNCH_POLL_SEC = 0.5

# stdout에 그대로 실을 본문의 길이 상한. 넘으면 파일로 빼고 경로만 알린다.
# 짧은 요약은 그대로 받고 긴 보고서만 파일로 가게 해서, 간단한 위임에 임시
# 파일을 만들었다 지우는 절차가 붙지 않도록 한다.
_RESULT_INLINE_LIMIT = 2000
_ERROR_INLINE_LIMIT = 2000

# 스킬을 배치할 디렉터리 이름. `.claude/skills/<이 이름>/SKILL.md` 가 된다.
_SKILL_NAME = "codex-shuttle"

_SANDBOXES = ("read-only", "workspace-write", "danger-full-access")
_POLICIES = ("never", "on-request", "untrusted")
_DECISIONS = ("accept", "acceptForSession", "decline", "cancel")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-shuttle", description=APP_NAME + " client"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("gui", help="Run the GUI app")
    sub.add_parser("health", help="Check whether the app is up")

    ensure = sub.add_parser(
        "ensure", help="Launch the app if it is down, then wait until it is ready"
    )
    ensure.add_argument(
        "--timeout", type=int, default=45, metavar="sec", help="How long to wait"
    )

    run = sub.add_parser("run", help="Submit a job")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt", help="The task text, inline")
    source.add_argument("--task", help="Path to a file holding the task")
    run.add_argument("--label", default="", help="Name shown in the job list")
    run.add_argument("--cwd", help="Folder codex works in")
    run.add_argument("--model")
    run.add_argument("--effort", help="low / medium / high / xhigh / max")
    run.add_argument("--sandbox", default="workspace-write", choices=_SANDBOXES)
    run.add_argument("--approval", default="never", choices=_POLICIES)
    run.add_argument("--approval-timeout", type=int, default=300, metavar="sec")
    run.add_argument("--on-timeout", default="decline", choices=_DECISIONS)
    run.add_argument(
        "--on-permission-timeout",
        choices=_DECISIONS,
        help="Handle elevation requests differently from other approvals",
    )
    run.add_argument("--wait", action="store_true", help="Wait until the job finishes")
    run.add_argument(
        "--output-path",
        metavar="path",
        help="File to write codex's final message to. Short bodies still ride in the result JSON",
    )
    run.add_argument("--client-id", default="", help="Identifier that tells sessions apart")

    cancel = sub.add_parser("cancel", help="Stop a running job")
    cancel.add_argument("job_id")
    cancel.add_argument("--client-id", default="")

    skill = sub.add_parser("install-skill", help="Install the Claude skill file")
    where = skill.add_mutually_exclusive_group(required=True)
    where.add_argument(
        "--user", action="store_true", help="Under ~/.claude (every project)"
    )
    where.add_argument(
        "--project",
        nargs="?",
        const=".",
        metavar="path",
        help="Under that project's .claude (defaults to the current folder)",
    )
    skill.add_argument(
        "--force", action="store_true", help="Overwrite an existing file"
    )
    return parser


def _force_utf8_output() -> None:
    """콘솔 코드페이지와 무관하게 UTF-8로 출력한다.

    Windows 콘솔 기본값은 CP949라, 결과에 한글이 들어 있으면 물음표로 바뀌거나
    UnicodeEncodeError로 죽는다. 클로드가 읽는 것이 이 stdout이므로 고정한다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    if args.command == "gui":
        return _run_gui()
    if args.command == "install-skill":
        return _install_skill(args)

    # Qt 소켓을 쓰려면 코어 애플리케이션 객체가 하나 있어야 한다. 창은 띄우지 않는다.
    QCoreApplication(sys.argv[:1])
    if args.command == "ensure":
        return _ensure(args.timeout)
    try:
        return _dispatch(args)
    except NotRunningError as error:
        print(str(error), file=sys.stderr)
        return EXIT_NOT_RUNNING
    except ConnectionLostError as error:
        print(str(error), file=sys.stderr)
        return EXIT_NOT_RUNNING
    except (TimeoutError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_USAGE


def _run_gui() -> int:
    from codex_shuttle.app import run

    return run()


def _ensure(timeout_sec: int) -> int:
    """앱이 꺼져 있으면 띄우고, 창구가 열릴 때까지 기다린다.

    잡을 던지기 직전에 부르는 용도다. 이미 떠 있으면 health와 똑같이 동작한다.
    """
    deadline = time.monotonic() + max(1, timeout_sec)
    launched = False
    last: dict | None = None

    while time.monotonic() < deadline:
        health = _try_health()
        if health is None:
            if launched:
                time.sleep(_LAUNCH_POLL_SEC)
                continue
            print("CodexShuttle is down, launching it…", file=sys.stderr)
            try:
                _launch_gui()
            except OSError as error:
                print("Could not launch it: " + str(error), file=sys.stderr)
                return EXIT_NOT_RUNNING
            launched = True
            time.sleep(_LAUNCH_POLL_SEC)
            continue

        last = health
        # 소켓은 환경 조회보다 먼저 열린다. 잔여 한도를 모르는 채로 돌려주면
        # 한도 소진을 걸러낼 수 없으므로, 확인이 끝날 때까지 더 기다린다.
        if _is_settled(health):
            return _emit_health(health)
        time.sleep(_LAUNCH_POLL_SEC)

    if last is not None:
        print("The environment check did not settle. The values below are not final.", file=sys.stderr)
        return _emit_health(last)

    print(
        "Not ready within {0}s. Check whether the window opened.".format(
            timeout_sec
        ),
        file=sys.stderr,
    )
    return EXIT_NOT_RUNNING


def _is_settled(health: dict) -> bool:
    """환경 확인이 끝나 값을 믿을 수 있는 상태인지."""
    usage = health.get("usage")
    if not isinstance(usage, dict):
        return True
    return str(usage.get("status")) not in ("checking", "unknown", "", "None")


def _try_health() -> dict | None:
    """붙을 수 있으면 health 응답을, 아니면 None을 돌려준다."""
    try:
        with Connection() as connection:
            return connection.request("health")
    except (NotRunningError, ConnectionLostError, TimeoutError, OSError, ValueError):
        return None


def _launch_gui() -> None:
    """GUI를 떼어 내서 띄운다.

    이 CLI 프로세스가 끝나도 앱은 살아 있어야 하므로 세션에서 분리한다. 그렇게
    하지 않으면 클로드가 명령을 끝내는 순간 앱도 같이 죽는다.
    """
    # 앱 안에서도 문자열 처리가 UTF-8로 고정되도록 넘겨 준다.
    environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    options: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": environment,
    }
    if sys.platform == "win32":
        options["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True

    subprocess.Popen([_gui_interpreter(), "-m", "codex_shuttle"], **options)


def _gui_interpreter() -> str:
    """Windows에서는 콘솔 창이 따라 뜨지 않도록 pythonw를 쓴다."""
    if sys.platform != "win32":
        return sys.executable
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


def _dispatch(args: argparse.Namespace) -> int:
    with Connection() as connection:
        if args.command == "health":
            return _emit_health(connection.request("health"))
        if args.command == "cancel":
            return _emit_ack(
                connection.request(
                    "cancel", job_id=args.job_id, client_id=args.client_id
                )
            )
        if args.command == "run":
            return _run_job(connection, args)
    return EXIT_USAGE


def _run_job(connection: Connection, args: argparse.Namespace) -> int:
    spec = {
        "prompt": _read_prompt(args),
        "label": args.label,
        "cwd": args.cwd,
        "model": args.model,
        "effort": args.effort,
        "sandbox": args.sandbox,
        "approval_policy": args.approval,
        "approval_timeout_sec": args.approval_timeout,
        "approval_timeout_decision": args.on_timeout,
    }
    if args.on_permission_timeout:
        spec["permission_timeout_decision"] = args.on_permission_timeout

    ack = connection.request(
        "submit", spec=spec, wait=bool(args.wait), client_id=args.client_id
    )
    if not ack.get("ok"):
        print(ack.get("error") or "Submit failed.", file=sys.stderr)
        return EXIT_USAGE

    if not args.wait:
        return _emit_ack(ack)

    # --wait 중에는 잡이 끝나기 전까지 stdout에 아무것도 나가지 않는다.
    # 중단하려면 job_id가 필요하므로 제출 직후 stderr로 흘려 준다.
    print("job_id: " + str(ack.get("job_id") or ""), file=sys.stderr, flush=True)

    # 여기서 잡이 끝날 때까지 멈춰 있는다. 이 프로세스의 종료가 곧 완료 통지다.
    done = connection.read_message(NO_TIMEOUT)
    job = done.get("job") or {}
    clean = _clean_job(job, args.output_path)

    print(json.dumps(clean, ensure_ascii=False, indent=2))
    # stdout JSON에 이미 오류가 들어 있다. stderr에는 중복해서 싣지 않고,
    # 파일로 뺀 경우에만 어디를 봐야 하는지 한 줄로 알린다.
    if clean.get("error_path"):
        print("Full error: " + clean["error_path"], file=sys.stderr)
    elif clean.get("error"):
        print(clean["error"], file=sys.stderr)
    return _STATE_EXIT.get(str(job.get("state")), EXIT_JOB_FAILED)


def _install_skill(args: argparse.Namespace) -> int:
    """패키지에 들어 있는 SKILL.md를 클로드가 읽는 자리로 복사한다.

    저장소를 체크아웃한 위치와 무관하게 동작하도록 패키지 데이터에서 꺼낸다.
    """
    if args.user:
        root = Path.home()
    else:
        root = Path(args.project).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("No such folder: " + str(root))

    target = root / ".claude" / "skills" / _SKILL_NAME / "SKILL.md"
    if target.exists() and not args.force:
        print("Already there: " + str(target), file=sys.stderr)
        print("Pass --force to overwrite.", file=sys.stderr)
        return EXIT_USAGE

    body = (
        resources.files("codex_shuttle").joinpath("skill/SKILL.md").read_text("utf-8")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    print("Installed the skill at: " + str(target))
    print("Claude sessions already open have to restart to see it.")
    return EXIT_OK


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        return args.prompt
    path = Path(args.task).expanduser()
    if not path.is_file():
        raise ValueError("No such task file: " + str(path))
    return path.read_text(encoding="utf-8")


def _write_output(target: str | None, body: str) -> str | None:
    """본문을 파일에 쓰고 그 경로를 돌려준다. 쓸 것이 없으면 None."""
    if not target or not body:
        return None
    path = Path(target).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return str(path)


def _clean_job(job: dict, output_path: str | None = None) -> dict:
    """stdout으로 내보낼 잡 정보.

    codex 최종 메시지(`result`)는 길이 제한이 없어 stdout을 통째로 먹을 수 있다.
    그래서 짧으면 그대로 싣고, 길면 파일로 빼서 경로만 알린다. 오류도 같은
    규칙을 따른다. 부르는 쪽이 길이를 미리 알 필요가 없다.

    """
    body = job.get("result") or ""
    clean = {key: value for key, value in job.items() if key != "result"}
    clean["result_chars"] = len(body)

    # 명시적으로 요청한 파일은 길이와 무관하게 쓴다.
    path = _write_output(output_path, body)
    if len(body) <= _RESULT_INLINE_LIMIT:
        clean["result"] = body
    else:
        clean["result"] = None
        if path is None:
            path = _write_output(str(_spill_path(job, None, "result")), body)
    clean["result_path"] = path

    clean["error"], clean["error_path"] = _clean_error(
        str(job.get("error") or ""), job, output_path
    )
    return clean


def _clean_error(
    error: str, job: dict, output_path: str | None
) -> tuple[str, str | None]:
    """긴 오류는 파일로 빼고 앞부분과 경로만 돌려준다.

    `job.error`는 오류가 날 때마다 이어 붙는 누적 문자열이라 상한이 없다.
    """
    if len(error) <= _ERROR_INLINE_LIMIT:
        return error, None

    head = error[:_ERROR_INLINE_LIMIT]

    path = _spill_path(job, output_path, "error")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(error, encoding="utf-8")
    except OSError as failure:
        # 파일을 못 쓰면 잘라서라도 알린다. 오류 보고가 또 실패해선 안 된다.
        return error[:_ERROR_INLINE_LIMIT] + "\n…(could not save the full text: {0})".format(
            failure
        ), None

    return (
        "{0}\n…({1:,} chars total. Full text: {2})".format(head, len(error), path),
        str(path),
    )


def _spill_path(job: dict, output_path: str | None, kind: str) -> Path:
    """긴 내용을 쓸 자리. 결과 파일이 있으면 그 옆, 없으면 임시 폴더."""
    suffix = "." + kind + ".txt"
    if output_path:
        base = Path(output_path)
        return base.with_name(base.name + suffix)
    job_id = str(job.get("job_id") or "unknown")
    return Path(tempfile.gettempdir()) / "codex-shuttle" / (job_id + suffix)


def _emit_health(response: dict) -> int:
    """codex가 작업을 받을 수 있는 상태인지 알려 준다.

    설치·로그인·한도 중 하나라도 막혀 있으면 종료 코드 5로 끝난다. 스킬이 잡을
    던지기 전에 이 값으로 걸러 낼 수 있다.
    """
    if not response.get("ok"):
        print(response.get("error") or "The request failed.", file=sys.stderr)
        return EXIT_USAGE

    print(json.dumps(response, ensure_ascii=False, indent=2))
    if response.get("ready") is False:
        for blocker in response.get("blockers") or []:
            print(blocker, file=sys.stderr)
        return EXIT_NOT_READY
    return EXIT_OK


def _emit_ack(response: dict) -> int:
    """제출·중단의 짧은 확인. 본문은 싣지 않는다."""
    if not response.get("ok"):
        print(response.get("error") or "The request failed.", file=sys.stderr)
        return EXIT_USAGE
    job = response.get("job") or {}
    print(
        json.dumps(
            {
                "job_id": response.get("job_id") or job.get("job_id"),
                "state": job.get("state"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
