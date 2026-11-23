# 模块 53：Chrono 子进程环境隔离与 ABI 预检（2026-11-03）

修复类型：缺陷修复（扩展模块：模态/Chrono/载荷传递）。不改变任何求解链路、
场景算例与锚点；chrono-env 独立进程架构保持不变。

## 1. 根因（实读 + 实测复现）

两门失败（`verify_chrono_load_transfer.py`、`verify_extensions_gui.py`）的根因：
**宿主进程的 `PYTHONPATH` 经 `os.environ` 泄漏进 chrono-env（Python 3.12）子进程**，
numpy 解析到 cp313 二进制 → `ImportError`。

- 原实现 `chrono_adapter.py`（修前实读）：`env=dict(os.environ, PYTHONUTF8="1",
  TEMP=…, TMP=…)` 只补 PATH 与编码变量，未清洗 PYTHONPATH/PYTHONHOME。
- 本机泄漏源（实测）：宿主 shell `PYTHONPATH=E:\Codex\python-packages`
  （含 cp313 numpy 2.5.3）；指向 `E:/rm_sim/venv/Lib/site-packages` 时同样有毒。
- 复现证据（2026-11-03，`E:/rm_sim/temp/chrono_repro/stderr.log`）：
  cp312 解释器（`E:\rm_sim\chrono-env\python.exe`）加载
  `_multiarray_umath.cp313-win_amd64.pyd`（numpy "2.5.3"）→
  `ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'` →
  `ImportError`。与任务书基线材料一致。
- 历史留档：Nov-2 晚间证据 `E:/rm_sim/verification/chrono_20261102_222504_775873/
  motion/chrono_stderr.log` 已是同一 ImportError（numpy 路径 `E:\Codex\python-packages`）；
  Nov-2 上午运行（`chrono_20261102_100748_057136`）在无泄漏宿主下 PASS。

## 2. venv 安装路线试装结论（如实记录）

任务书提到的「venv 安装路线」实测不可行，**以 chrono-env 为准**（未改动主 venv）：

- 实测命令：`E:/rm_sim/venv/Scripts/python.exe -m pip install --dry-run --no-deps
  --cache-dir E:/rm_sim/pipcache pychrono` → pip（清华镜像）解析到
  **`pychrono-1.1.0-py3-none-any.whl`（10 kB 纯 Python 占位包）**——与
  Project Chrono 的 PyChrono 10（平台 wheel，cp312，安装在 chrono-env 的
  `_core.pyd` 等原生扩展）**不是同一项目**。cp313 无 PyChrono 10 wheel。
- 结论：主 venv（cp313）不装 pychrono；chrono-env（Python 3.12.16 + numpy
  2.5.4 + PyChrono 10.0.0，config `chronoPython`）保持唯一 Chrono 运行环境。

## 3. 修复内容

### 3.1 子进程 env 清洗 + 显式隔离（`solver/chrono_adapter.py`）

新函数 `chrono_isolated_env(python_dir)`：

- 剥离宿主全部 `PYTHON*` 变量（PYTHONPATH/PYTHONHOME/PYTHONUSERBASE/…）；
- 显式设置 `PYTHONUTF8=1`（原行为）、`PYTHONNOUSERSITE=1`（禁用户 site）、
  `PYTHONSAFEPATH=1`（不把脚本目录/cwd 前插 sys.path）；
- PATH 前缀仍为 chrono-env `Library/bin` + chrono-env；其余系统变量原样继承
  （Windows DLL 加载需要 SYSTEMROOT 等）。`run_chrono` 改用该环境。

### 3.2 ABI 预检（`solver/chrono_scene.py` + `chrono_adapter.py`）

- 期望 tag（父进程）：`chrono_expected_abi_tag` 向 chrono-env 解释器本体实问
  `sysconfig.get_config_var('EXT_SUFFIX')`（`.cp312-win_amd64.pyd` → `cp312`），
  落盘 `chrono_interpreter.json`；探针只读 stdlib、不导入 numpy。
- 实际 tag（子进程）：`chrono_scene.environment_report` 在**任何计算之前**
  先导入 numpy/pychrono，把两者版本、扩展模块路径、已加载扩展 ABI tag
  （regex `(cp\d{2,})-win_amd64` 扫 sys.modules）与 sys.path 打印为
  `CHRONO_ENV_REPORT …` 行（落入 `chrono_stdout.log`）并写
  `chrono_env_report.json`。导入失败：报告带 `error` 字段 + stdout 打
  `CHRONO_ENV_PRECHECK_FAILED` + **exit 3**（ImportError 不再裸奔 stderr）。
- 判定（父进程）：`check_chrono_environment` 在解析任何结果之前执行——
  报告缺失 / 带 error / `abi_tags` ≠ 期望 tag / numpy/pychrono 模块路径越出
  chrono-env 目录（normcase+`os.sep` 前缀）→ 各自专属 `RuntimeError`。
  场景 exit 3 时报环境错误而非笼统「执行失败」。
- 注意：pychrono 的 `_core.pyd` 文件名无 ABI tag——其 tag 由「导入成功」
  本身证明（ABI 不符时 .pyd 根本无法加载）；tag 断言的主对象是 numpy 扩展。

### 3.3 保持的架构与不变量

- `run_chrono`/`transfer_load` 签名与返回结构不变；场景算例（单摆/电机/
  弹簧/碰撞）、单位、锚点全部零改动；`chrono_results.json`/报告格式不变；
  config 默认 `chronoPython=E:/rm_sim/chrono-env/python.exe` 不变。

## 4. 平台怪癖（实测，验收须遵守）

**chrono-env python 从 MSYS/git-bash 直接启动会挂起**（本机实测 3 进程：
0.3s CPU 后静止双线程，含 import 前失败场景；kill 后解除）。产品链路与全部
验收都经 Windows 父进程（venv python）spawn，不受影响。勿在 bash 里直跑
`E:/rm_sim/chrono-env/python.exe`。

## 5. 验收（`verify/verify_chrono_env_isolation.py`，exit 0 = 通过）

2026-11-03 实测 exit 0（宿主 env 含真实泄漏源 `PYTHONPATH=E:\Codex\python-packages`）：

- ①a `chrono_isolated_env` 单元：恶意 PYTHONPATH/PYTHONHOME 注入后子进程 env
  仅剩三个显式开关、PATH 前缀正确、tag 提取正确；
- ①b 产品路径：宿主注入 `PYTHONPATH=E:/rm_sim/venv/Lib/site-packages`（cp313
  numpy 2.5.3）+ PYTHONHOME → `run_chrono` 完整场景成功（单摆 1.639s 偏差<3%），
  子进程 numpy=**2.5.4 cp312**（chrono-env 自身版本），numpy/pychrono 模块路径
  全部在 chrono-env 内，stderr 无 ImportError；
- ③ `chrono_stdout.log` 含 `CHRONO_ENV_REPORT` 行（版本+ABI tag 写日志）；
- ②a 咬合负例：绕过 run_chrono、恶意 PYTHONPATH 直跑场景 → 秒级 **exit 3** +
  `CHRONO_ENV_PRECHECK_FAILED` + 报告 error 记录 ImportError 与 cp313 pyd 现场
  （证明恶意值真有毒、预检立即报错）；
- ②b 判定负例：伪造报告 tag=cp313 / 报告缺失 / 带 error / 模块越界 → 各自
  RuntimeError；
- ④a `verify_chrono_load_transfer.py` 单跑 **exit 0 + PASS**（period=1.639s、
  RF+F 残差<0.01N、Uy=-0.190265mm；既有锚点单摆<3%/弹簧 x(1s)<0.005 m/
  旋转向 allclose/provenance sha256 一致由该脚本自身断言）；
- ④b `verify_extensions_gui.py` 单跑（offscreen）**exit 0 + PASS**；
- ④c 全部子进程 stderr 无 `ImportError`。

证据目录：`E:/rm_sim/verification/chrono_env_isolation_20261103_202821_712594`；
日志：`verify/logs/verify_chrono_env_isolation.log`。
