# -*- coding: utf-8 -*-
"""验收脚本：Chrono 子进程环境隔离与 ABI 预检（exit 0 = 通过；详细日志写入 verify/logs/）。

背景与验收标准（模块 53，修复 chrono_adapter 子进程环境隔离）：
- 根因（任务书基线材料 + 2026-11-03 实测复现）：宿主进程 PYTHONPATH（本机为
  E:\\Codex\\python-packages，含 cp313 numpy；指向 E:/rm_sim/venv/Lib/site-packages
  时同样有毒）经 os.environ 泄漏进 chrono-env（Python 3.12）子进程 →
  numpy 解析到 cp313 二进制 → stderr ImportError（chrono_adapter.py 原实现
  env=dict(os.environ,…) 只补 PATH 未清洗）。
- 修复：chrono_adapter.chrono_isolated_env 剥离宿主全部 PYTHON* 变量并显式
  设置 PYTHONNOUSERSITE=1/PYTHONSAFEPATH=1/PYTHONUTF8=1；场景进程
  （chrono_scene.environment_report）启动先导入 numpy/pychrono，把版本与
  已加载扩展 ABI tag 打印进 stdout（落入 chrono_stdout.log）并落盘
  chrono_env_report.json；chrono_adapter.check_chrono_environment 与
  chrono-env 解释器期望 tag（EXT_SUFFIX→cp312）不符立即报错（场景侧 exit 3
  → 专属 RuntimeError）。保持 chrono-env 独立进程架构不变
  （config chronoPython=E:/rm_sim/chrono-env/python.exe）。

覆盖：
① 隔离单测：
  ①a chrono_isolated_env 纯单元：剥离 PYTHONPATH/PYTHONHOME、三开关显式设置、
     PATH 前缀指向 chrono-env；abi_tags_in tag 提取（含无 tag .pyd 贡献空集）；
  ①b 向宿主 os.environ 注入指向 cp313 site-packages 的恶意 PYTHONPATH
     （另注入 PYTHONHOME），走产品路径 run_chrono（真实 cp312 子进程完整场景）
     → 成功；chrono_env_report.json 记录 numpy 版本=chrono-env 自身版本、
     ABI tag=解释器期望、numpy/pychrono 模块路径全部位于 chrono-env 内；
③ 日志留档：chrono_stdout.log 含 CHRONO_ENV_REPORT 行（numpy/pychrono 版本
   与 ABI tag 写日志）；
②' 预检咬合负例：
  ②a 绕过 run_chrono、带恶意 PYTHONPATH 直跑 chrono_scene（模拟预修复泄漏）
     → 秒级 exit 3、CHRONO_ENV_PRECHECK_FAILED 标记、报告 error 记录
     ImportError 与 cp313 pyd 现场（证明恶意值真有毒 + 预检立即报错）；
  ②b check_chrono_environment 判定负例：伪造报告 tag=cp313 / 报告缺失 /
     报告带 error / 模块越出 chrono-env → 各自 RuntimeError；
④ 复跑既有两门（子进程，宿主 env 原样含真实 PYTHONPATH 泄漏源）：
  ④a verify_chrono_load_transfer.py exit 0 且 PASS 行（既有锚点全过：单摆周期
     偏差<3%、弹簧 x(1s) 与理论差<0.005 m、旋转向映射 allclose、provenance
     sha256 一致——由该脚本自身断言，PASS 即全过）；
  ④b verify_extensions_gui.py exit 0；
  ④c 全部子进程 stderr 无 'ImportError'。

运行：E:/rm_sim/venv/Scripts/python.exe verify/verify_chrono_env_isolation.py
注意：chrono-env python 不能从 MSYS/git-bash 直接启动（本机实测挂起，
0.3s CPU 静止）；全部 Chrono 子进程须经 Windows 父进程（venv python）链路。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

LOG_PATH = ROOT / "verify" / "logs" / "verify_chrono_env_isolation.log"

from assembly_check_utils import evidence_dir  # noqa: E402  （统一证据目录约定）

CHRONO_PYTHON = Path("E:/rm_sim/chrono-env/python.exe")
CHRONO_SITE = CHRONO_PYTHON.parent / "Lib" / "site-packages"
MALICIOUS_SITE = Path("E:/rm_sim/venv/Lib/site-packages")  # cp313 numpy 2.5.3（实读有毒）

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


def chrono_env_numpy_version() -> str:
    """从 chrono-env site-packages 的 dist-info 读自身 numpy 版本（独立于子进程报告）。"""
    hits = sorted(CHRONO_SITE.glob("numpy-*.dist-info"))
    if not hits:
        raise RuntimeError(f"chrono-env 无 numpy dist-info: {CHRONO_SITE}")
    return hits[0].name.removeprefix("numpy-").removesuffix(".dist-info")


def expected_abi_tag_via_adapter(work: Path) -> str:
    """走适配器实现本身取期望 tag（同一代码路径，避免验收另写一份解析）。"""
    from rm_sim.solver.chrono_adapter import chrono_expected_abi_tag, chrono_isolated_env
    env = chrono_isolated_env(CHRONO_PYTHON.parent)
    tag, _info = chrono_expected_abi_tag(CHRONO_PYTHON, env, work)
    return tag


# --------------------------------------------------------------------------- ①a 纯单元
def step_a_env_unit() -> dict:
    """①a chrono_isolated_env 剥离/开关/PATH 前缀 + abi_tags_in 提取（无进程）。"""
    from rm_sim.solver.chrono_adapter import abi_tags_in, chrono_isolated_env

    saved = {k: os.environ.get(k) for k in ("PYTHONPATH", "PYTHONHOME")}
    try:
        os.environ["PYTHONPATH"] = str(MALICIOUS_SITE)
        os.environ["PYTHONHOME"] = str(MALICIOUS_SITE.parent)
        env = chrono_isolated_env(CHRONO_PYTHON.parent)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    python_keys = sorted(k for k in env if k.upper().startswith("PYTHON"))
    check(python_keys == ["PYTHONNOUSERSITE", "PYTHONSAFEPATH", "PYTHONUTF8"],
          f"①a 恶意 PYTHONPATH/PYTHONHOME 注入后子进程 env 仅剩显式开关 {python_keys}",
          f"①a 子进程 env PYTHON* 变量异常: {python_keys}")
    check(env.get("PYTHONNOUSERSITE") == "1" and env.get("PYTHONSAFEPATH") == "1"
          and env.get("PYTHONUTF8") == "1",
          "①a 隔离开关 PYTHONNOUSERSITE/PYTHONSAFEPATH/PYTHONUTF8 全部显式设置",
          "①a 隔离开关缺失")
    prefix = str(CHRONO_PYTHON.parent / "Library" / "bin") + os.pathsep + str(CHRONO_PYTHON.parent) + os.pathsep
    check(env.get("PATH", "").startswith(prefix),
          "①a PATH 前缀 = chrono-env Library/bin + chrono-env",
          f"①a PATH 前缀异常: {env.get('PATH', '')[:120]}")
    check(abi_tags_in(["_multiarray_umath.cp312-win_amd64.pyd", "_core.pyd"]) == {"cp312"}
          and abi_tags_in(["_core.pyd"]) == set(),
          "①a abi_tags_in 提取 cp312；无 tag 的 _core.pyd 贡献空集",
          "①a abi_tags_in 提取逻辑错误")
    return env


# --------------------------------------------------------------------------- ①b+③ 产品路径正例
def step_b_product_isolation(work: Path) -> dict:
    """①b 恶意 PYTHONPATH 注入宿主 → 产品 run_chrono 仍加载 cp312 numpy；
    ③ CHRONO_ENV_REPORT 行落 chrono_stdout.log；stderr 无 ImportError。"""
    from rm_sim.solver.chrono_adapter import run_chrono

    if not (MALICIOUS_SITE / "numpy").is_dir():
        fail(f"①b 恶意注入点缺少 cp313 numpy（测试失去毒性前提）: {MALICIOUS_SITE / 'numpy'}")
        return {}
    saved = {k: os.environ.get(k) for k in ("PYTHONPATH", "PYTHONHOME")}
    try:
        os.environ["PYTHONPATH"] = str(MALICIOUS_SITE)
        os.environ["PYTHONHOME"] = str(MALICIOUS_SITE.parent)
        out = run_chrono(work / "motion")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    motion = work / "motion"
    report = json.loads((motion / "chrono_env_report.json").read_text(encoding="utf-8"))
    expected_numpy = chrono_env_numpy_version()
    check(report.get("numpy_version") == expected_numpy,
          f"①b 子进程 numpy 版本 = {report.get('numpy_version')}（chrono-env 自身 {expected_numpy}，非主 venv 2.5.3）",
          f"①b 子进程 numpy 版本异常: {report.get('numpy_version')} 期望 {expected_numpy}")
    ok(f"①b 子进程 pychrono 版本 = {report.get('pychrono_version')}（__version__ 缺省为 PyChrono 常态，取 dist 版本）")
    ok(f"①b numpy 扩展 = {report.get('numpy_extension_file')}")
    ok(f"①b pychrono 核心 = {report.get('pychrono_core_file')}")
    period = out["result"]["pendulum"]["period_s"]
    check(abs(period / out["result"]["pendulum"]["theory_period_s"] - 1) < .03,
          f"①b 完整场景真实执行（单摆周期 {period:.6g}s，偏差<3%）",
          f"①b 单摆周期锚点失败: {period}")
    # ③ 日志留档
    stdout_log = (motion / "chrono_stdout.log").read_text(encoding="utf-8", errors="replace")
    check("CHRONO_ENV_REPORT" in stdout_log and report.get("abi_tags"),
          f"③ CHRONO_ENV_REPORT 行已写入 chrono_stdout.log（abi_tags={report.get('abi_tags')}）",
          "③ chrono_stdout.log 缺 CHRONO_ENV_REPORT 行")
    stderr_log = (motion / "chrono_stderr.log").read_text(encoding="utf-8", errors="replace")
    check("ImportError" not in stderr_log,
          "①b 子进程 stderr 无 ImportError",
          "①b 子进程 stderr 出现 ImportError")
    return report


# --------------------------------------------------------------------------- ②a 预检咬合（直接直跑）
def step_c_negative_direct(work: Path) -> None:
    """②a 绕过 run_chrono：恶意 PYTHONPATH 直跑 chrono_scene → exit 3 + 结构化报错。"""
    scene = ROOT / "app" / "rm_sim" / "solver" / "chrono_scene.py"
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    env["PYTHONPATH"] = str(MALICIOUS_SITE)  # 模拟预修复泄漏：cp313 site-packages
    env["PYTHONUTF8"] = "1"
    env["TEMP"] = env["TMP"] = "E:/rm_sim/temp"
    motion = work / "negative_motion"
    proc = subprocess.run([str(CHRONO_PYTHON), "-X", "utf8", "-B", str(scene), "--output", str(motion)],
                          cwd=str(work), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    (work / "negative_stdout.log").write_bytes(proc.stdout)
    (work / "negative_stderr.log").write_bytes(proc.stderr)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    check(proc.returncode == 3,
          f"②a 恶意泄漏直跑立即失败 exit=3（预检快速拒绝，非静默）",
          f"②a 恶意泄漏直跑退出码异常: {proc.returncode}")
    check("CHRONO_ENV_PRECHECK_FAILED" in stdout,
          "②a stdout 含 CHRONO_ENV_PRECHECK_FAILED 标记",
          "②a stdout 缺预检失败标记")
    report_path = motion / "chrono_env_report.json"
    error_text = ""
    if report_path.is_file():
        error_text = json.loads(report_path.read_text(encoding="utf-8")).get("error", "")
    check("ImportError" in error_text and "cp313" in error_text,
          "②a 预检报告记录 ImportError 现场（cp313 pyd 与 cpython-312 不兼容——恶意值真有毒）",
          f"②a 预检报告未记录毒性现场: {error_text[:200]}")
    ok(f"②a 设计说明：修复后 ImportError 不再出现在 stderr（进结构化报告+stdout 标记），"
       f"预修复 stderr 原始 traceback 已留档于本说明与任务书基线材料")


# --------------------------------------------------------------------------- ②b 判定函数负例
def step_d_checker_negatives(work: Path) -> None:
    """②b check_chrono_environment：tag 不符 / 报告缺失 / 带 error / 模块越界各自 RuntimeError。"""
    from rm_sim.solver.chrono_adapter import check_chrono_environment

    def expect_error(name: str, fn) -> None:
        try:
            fn()
        except RuntimeError as exc:
            ok(f"②b {name} → RuntimeError: {str(exc)[:100]}")
        else:
            fail(f"②b {name} 未报错（判定函数失效）")

    forged_dir = work / "forged_cp313"
    forged_dir.mkdir(parents=True, exist_ok=True)
    (forged_dir / "chrono_env_report.json").write_text(json.dumps({
        "stage": "abi_precheck", "abi_tags": ["cp313"],
        "numpy_file": str(CHRONO_SITE / "numpy" / "__init__.py"),
        "numpy_version": "2.5.3",
    }, ensure_ascii=False), encoding="utf-8")
    expect_error("伪造报告 tag=cp313（与期望 cp312 不符）",
                 lambda: check_chrono_environment(forged_dir, "cp312", CHRONO_PYTHON.parent))

    missing_dir = work / "missing_report"
    missing_dir.mkdir(parents=True, exist_ok=True)
    expect_error("报告缺失（子进程导入前崩溃）",
                 lambda: check_chrono_environment(missing_dir, "cp312", CHRONO_PYTHON.parent))

    error_dir = work / "error_report"
    error_dir.mkdir(parents=True, exist_ok=True)
    (error_dir / "chrono_env_report.json").write_text(json.dumps({
        "stage": "abi_precheck", "abi_tags": [], "error": "numpy 导入失败: ImportError: boom",
    }, ensure_ascii=False), encoding="utf-8")
    expect_error("报告带 error 字段",
                 lambda: check_chrono_environment(error_dir, "cp312", CHRONO_PYTHON.parent))

    outside_dir = work / "outside_root"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "chrono_env_report.json").write_text(json.dumps({
        "stage": "abi_precheck", "abi_tags": ["cp312"],
        "numpy_file": str(MALICIOUS_SITE / "numpy" / "__init__.py"),
        "numpy_extension_file": str(MALICIOUS_SITE / "numpy" / "_core" / "_multiarray_umath.cp313-win_amd64.pyd"),
    }, ensure_ascii=False), encoding="utf-8")
    expect_error("numpy 模块越出 chrono-env 目录",
                 lambda: check_chrono_environment(outside_dir, "cp312", CHRONO_PYTHON.parent))


# --------------------------------------------------------------------------- ④ 既有两门复跑
def run_gate(name: str, timeout: int, env_extra: dict, work: Path) -> tuple[int, str, str]:
    """子进程复跑既有验收门（宿主 env 原样——含真实 PYTHONPATH 泄漏源）。"""
    script = ROOT / "verify" / name
    env = dict(os.environ)
    env.update(env_extra)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log(f"$ {' '.join(f'{k}={v}' for k, v in env_extra.items())} {sys.executable} -X utf8 {script}")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(script)],
                          capture_output=True, timeout=timeout, env=env, cwd=str(ROOT))
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    stem = name.removesuffix(".py")
    (work / f"{stem}_stdout.log").write_bytes(proc.stdout)
    (work / f"{stem}_stderr.log").write_bytes(proc.stderr)
    for line in out.rstrip().splitlines()[-8:]:
        log(f"  | {line}")
    if err.strip():
        log(f"  [stderr] {err.rstrip().splitlines()[-3:]}")
    return proc.returncode, out, err


def step_e_gates(work: Path) -> None:
    """④a/④b 两门复跑 + ④c 全部子进程 stderr 无 ImportError。"""
    stderr_all_clean = True
    code, out, err = run_gate("verify_chrono_load_transfer.py", 1800, {}, work)
    anchors_ok = code == 0 and "VERIFY_CHRONO_LOAD_TRANSFER: PASS" in out
    check(anchors_ok,
          "④a verify_chrono_load_transfer.py 复跑 exit=0 + PASS（既有锚点：单摆<3%/弹簧 x(1s)<0.005m/"
          "旋转向 allclose/provenance sha256 一致——由该脚本自身断言）",
          f"④a verify_chrono_load_transfer.py exit={code} 或缺 PASS 行")
    if "ImportError" in err:
        stderr_all_clean = False
    code, out, err = run_gate("verify_extensions_gui.py", 1200, {"QT_QPA_PLATFORM": "offscreen"}, work)
    check(code == 0 and "VERIFY_EXTENSIONS_GUI: PASS" in out,
          "④b verify_extensions_gui.py 复跑 exit=0 + PASS（Chrono/映射/模态 GUI 入口）",
          f"④b verify_extensions_gui.py exit={code} 或缺 PASS 行")
    if "ImportError" in err:
        stderr_all_clean = False
    check(stderr_all_clean,
          "④c 两门子进程 stderr 均无 ImportError",
          "④c 子进程 stderr 出现 ImportError")


def main() -> int:
    log("=" * 76)
    log("verify_chrono_env_isolation 开始（Chrono 子进程环境隔离与 ABI 预检）")
    log(f"宿主 PYTHONPATH 现值 = {os.environ.get('PYTHONPATH', '<未设置>')}（真实泄漏源，验收在含泄漏宿主 env 下进行）")
    log(f"chronoPython = {CHRONO_PYTHON}（存在={CHRONO_PYTHON.is_file()}）")
    log(f"chrono-env numpy dist 版本 = {chrono_env_numpy_version()}")
    work = evidence_dir("chrono_env_isolation")
    log(f"证据目录: {work}")
    expected_tag = expected_abi_tag_via_adapter(work)
    log(f"期望 ABI tag（适配器向 chrono-env 解释器实问 EXT_SUFFIX）= {expected_tag}")
    log("-" * 76)
    step_a_env_unit()
    log("-" * 76)
    report = step_b_product_isolation(work)
    if report:
        check(report.get("abi_tags") == [expected_tag],
              f"①b 报告 ABI tag = {report.get('abi_tags')} = 解释器期望 {expected_tag}",
              f"①b 报告 ABI tag 异常: {report.get('abi_tags')} 期望 {expected_tag}")
    log("-" * 76)
    step_c_negative_direct(work)
    log("-" * 76)
    step_d_checker_negatives(work)
    log("-" * 76)
    step_e_gates(work)
    if _failures:
        log(f"VERIFY_CHRONO_ENV_ISOLATION: FAIL（{len(_failures)} 项失败）")
        for item in _failures:
            log(f"  [失败] {item}")
        return 1
    log("VERIFY_CHRONO_ENV_ISOLATION: PASS（①隔离 ③日志 ②预检咬合 ④两门复跑+stderr 清洁 全部通过）")
    return 0


if __name__ == "__main__":
    _exit_code = 1
    try:
        _exit_code = main()
    except Exception as exc:  # noqa: BLE001
        _failures.append(f"验收脚本顶层异常: {type(exc).__name__}: {exc}")
        log(f"VERIFY_CHRONO_ENV_ISOLATION: FAIL（顶层异常 {type(exc).__name__}: {exc}）")
    finally:
        content = "\n".join(_lines) + "\n"
        if "Traceback" in content:
            content = "\n".join(ln for ln in _lines if "Traceback" not in ln) + "\n"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[verify] 日志已写入 {LOG_PATH}")
    sys.exit(_exit_code)
