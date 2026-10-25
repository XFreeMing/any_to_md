"""Source-checkout entry point for the TV-01 model preparation experiment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


SOURCES = ("huggingface", "hf-mirror")


def signal_download(process: subprocess.Popen, sig: int) -> None:
    """Signal only the process group created by this launcher."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        elif sig == signal.SIGINT:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        elif sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass


def stop_download(process: subprocess.Popen) -> None:
    """Reap the launcher and stop its descendants even if uv exits first."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        for sig, seconds in ((signal.SIGINT, 10), (signal.SIGTERM, 3), (signal.SIGKILL if os.name == "posix" else signal.SIGTERM, 2)):
            signal_download(process, sig)
            deadline = time.monotonic() + seconds
            while True:
                process.poll()  # Reap uv so it cannot keep the group alive as a zombie.
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        return
                elif process.returncode is not None:
                    return
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        print("已向本次下载进程组发送终止信号，等待系统回收进程。", file=sys.stderr)
    finally:
        signal.signal(signal.SIGINT, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mineru4", description="TV-01：准备 MinerU 4 与本地标题 LLM 模型")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", aliases=["downlod"], help="下载、续传并校验模型（无需先运行 init）")
    download.add_argument("--source", choices=SOURCES, default="huggingface", help="显式选择来源，默认 huggingface；不会自动切源")
    mode = download.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只获取并固定文件清单，显示大小，不下载权重")
    mode.add_argument("--check", action="store_true", help="离线校验已固定清单中的全部本地文件")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[3]
    project = root / "scripts" / "mineru4"
    if not all(path.is_file() for path in (root / "pyproject.toml", project / "pyproject.toml", project / "uv.lock", project / "download.py")):
        print("mineru4 仅支持源码检出目录；请在仓库内运行 uv run mineru4 download。", file=sys.stderr)
        return 1
    state = root / "var" / "mineru4"
    # Do not inherit another project's Hub endpoint, token, cache or offline mode.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("HF_", "HUGGINGFACE_", "MINERU_"))}
    env.pop("VIRTUAL_ENV", None)
    env.update({
        "UV_PROJECT_ENVIRONMENT": str(state / "downloader"),
        "HF_HOME": str(state / "cache" / "huggingface"),
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PYTHONUNBUFFERED": "1",
    })
    command = [
        "uv", "run", *(["--offline"] if args.check else []), "--project", str(project), "--locked", "--python", "3.13", "--no-dev",
        "python", str(project / "download.py"), "--source", args.source,
    ]
    if args.dry_run:
        command.append("--dry-run")
    if args.check:
        command.append("--check")
    try:
        process = subprocess.Popen(
            command, cwd=root, env=env, start_new_session=os.name == "posix",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            result = process.wait()
        except KeyboardInterrupt:
            # Also forward SIGINT when only this launcher receives the signal.
            stop_download(process)
            print("下载已中断；保留缓存，重新执行同一命令可继续。", file=sys.stderr)
            return 130
        return 130 if result in (-2, 130) else (0 if result == 0 else 1)
    except OSError as exc:
        print(f"无法启动下载环境（需要 uv）：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
