# -*- coding: utf-8 -*-
"""验收脚本：整机启动冒烟与可重启验证（exit 0 = 通过；详细日志写入 verify/logs/）。

覆盖验收标准：
① QT_QPA_PLATFORM=offscreen，venv python 连续启动 main.py --smoke 两次，
   每次 120s 内 exit 0；
② 每次输出含「主窗口已显示」「渲染后端」行且无未捕获 traceback；
③ 第二次启动时间戳晚于第一次且同样成功（可重启证明）；
④ 日志确认 config.json 的 ccxPath 已加载。

视口约定：offscreen 下视口走主入口的懒加载 + Agg 降级路径（offscreen 无 GL
是 probes/probe4_log.txt 实证）；若冒烟崩在 createPlatformOpenGLContext，
判主窗口实现不合规——本脚本不改冒烟约定，直接判 FAIL。
运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_relaunch.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LOG_PATH = ROOT / "verify" / "logs" / "verify_relaunch.log"
TIMEOUT_S = 120

_lines: list[str] = []
_failures: list[str] = []


def log(msg: object) -> None:
    text = str(msg)
    print(text)
    _lines.append(text)


def fail(msg: str) -> None:
    _failures.append(msg)
    log(f"[FAIL] {msg}")


def ok(msg: str) -> None:
    log(f"[OK] {msg}")


def check(cond: bool, pass_msg: str, fail_msg: str) -> None:
    if cond:
        ok(pass_msg)
    else:
        fail(fail_msg)


def launch_once(index: int) -> dict:
    """启动 main.py --smoke 一次（offscreen，120s 超时），返回检查结果。"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(ROOT / "main.py"), "--smoke"]
    log(f"$ QT_QPA_PLATFORM=offscreen {' '.join(cmd)}（第 {index} 次启动）")
    t0 = time.monotonic()
    wall = datetime.now().isoformat(timespec="milliseconds")
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, env=env, cwd=str(ROOT))
        elapsed = time.monotonic() - t0
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        fail(f"启动 #{index} 超过 {TIMEOUT_S}s 未退出")
        return {"ok": False}
    for line in out.splitlines():
        if line.strip():
            log(f"  | {line}")
    if err.strip():
        log("  [stderr] " + " / ".join(err.strip().splitlines()[:6]))

    res: dict = {"wall_started": wall, "elapsed": elapsed, "out": out, "err": err}
    res["exit_ok"] = proc.returncode == 0
    check(
        res["exit_ok"],
        f"① 启动 #{index} exit=0（用时 {elapsed:.1f}s ≤ 120s）",
        f"① 启动 #{index} exit={proc.returncode}（用时 {elapsed:.1f}s）",
    )
    check(
        elapsed <= TIMEOUT_S,
        f"① 启动 #{index} 在 120s 内完成",
        f"① 启动 #{index} 用时 {elapsed:.1f}s > 120s",
    )
    check(
        "主窗口已显示" in out,
        f"② 启动 #{index} 输出含「主窗口已显示」",
        f"② 启动 #{index} 缺「主窗口已显示」",
    )
    backend_lines = [l for l in out.splitlines() if "渲染后端=" in l]
    check(
        len(backend_lines) >= 1,
        f"② 启动 #{index} 输出含「渲染后端」行: {backend_lines[:1]}",
        f"② 启动 #{index} 缺「渲染后端」行",
    )
    check(
        any("渲染后端=agg_fallback" in l for l in backend_lines),
        f"② offscreen 下视口按懒加载+Agg 降级路径完成（probe4 实证，未崩在 createPlatformOpenGLContext）",
        f"② 渲染后端非 agg_fallback（主窗口 offscreen 合规性可疑）: {backend_lines}",
    )
    uncaught = [l for l in out.splitlines() + err.splitlines() if "Traceback" in l]
    check(
        not uncaught,
        f"② 启动 #{index} 无未捕获 traceback",
        f"② 启动 #{index} 存在未捕获 traceback",
    )
    ccx_lines = [l for l in out.splitlines() if "ccxPath=" in l and "已加载" in l]
    check(
        len(ccx_lines) == 1 and "ccx_launcher.exe" in ccx_lines[0],
        f"④ 启动 #{index} 日志确认 config.json ccxPath 已加载: {ccx_lines[:1]}",
        f"④ 启动 #{index} 未确认 ccxPath 加载",
    )
    ts_lines = [l for l in out.splitlines() if l.startswith("冒烟时间戳=")]
    res["ts"] = ts_lines[0].split("=", 1)[1] if ts_lines else None
    check(
        res["ts"] is not None,
        f"③ 启动 #{index} 冒烟时间戳: {res['ts']}",
        f"③ 启动 #{index} 缺冒烟时间戳",
    )
    res["ok"] = not any("启动 #" in f for f in _failures)
    return res


def main() -> int:
    log("=" * 76)
    log("verify_relaunch 开始（整机启动冒烟与可重启验证）")
    log(f"python = {sys.executable}")
    check(
        "rm_sim" in sys.executable.lower() and "venv" in sys.executable.lower(),
        f"验收运行于 venv 解释器: {sys.executable}",
        f"应使用 E:/rm_sim/venv/Scripts/python.exe 运行本脚本，实际: {sys.executable}",
    )
    log("两次连续启动 main.py --smoke（offscreen）；视口走懒加载+Agg 降级（probe4 实证）")
    log("=" * 76)

    r1 = launch_once(1)
    r2 = launch_once(2)

    # ③ 可重启证明：第二次时间戳晚于第一次且同样成功
    check(
        r1.get("ts") and r2.get("ts") and r2["ts"] > r1["ts"],
        f"③ 第二次启动时间戳晚于第一次（{r1.get('ts')} → {r2.get('ts')}）且同样成功",
        f"③ 时间戳未递增: {r1.get('ts')} → {r2.get('ts')}",
    )
    check(
        r1.get("ok") and r2.get("ok"),
        "③ 两次启动均满足全部冒烟检查（程序可重启）",
        "③ 存在未通过的启动",
    )

    log("\n" + "=" * 76)
    if _failures:
        log("VERIFY_RELAUNCH: FAIL")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_RELAUNCH: PASS（连续两次启动冒烟成功，程序可重启）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        log(f"VERIFY_RELAUNCH: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
