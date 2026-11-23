# examples/

批处理算例规格。产物在运行后生成到 `examples/<算例>/`（已 gitignore，可随时重算）。

## 运行（仓库根目录，Windows PowerShell）

```powershell
.\.venv\Scripts\python.exe main.py --batch examples\part_beam.json
.\.venv\Scripts\python.exe main.py --batch examples\assembly_contact.json
.\.venv\Scripts\python.exe main.py --batch examples\modal_beam.json
.\.venv\Scripts\python.exe main.py --batch examples\chrono_demo.json   # 需要 Chrono 环境
```

| 规格 | 内容 |
|---|---|
| `part_beam.json` | 100×10×10 mm 悬臂梁、45 钢、端部 100 N |
| `assembly_contact.json` | 两件 20×20×20 mm 钢块、可分离接触 + 摩擦 μ=0.3、顶部 1000 N、各自重力 |
| `modal_beam.json` | 单零件模态（频率列表与振型） |
| `chrono_demo.json` | Chrono 刚体运动 → 单时刻载荷传递 → 梁静力 |

## 装配示例说明

`assembly_contact` 生成 `examples/assembly_contact/project.rmsimproject`，
可从软件「文件 → 打开工程」直接打开；原始输入、日志、DAT、FRD、VTU、报告和云图
在同目录的 `cases/case_NNN`。重复计算会创建新 case。

GUI 流程：导入 STEP →「工况 → 设置约束、载荷与连接」→ 选择部件与作用面、添加定义、
设置部件材料 →「求解 → 运行静力分析」→「装配与部件结果」切换真实部件场 → 保存工程。
工程重开后修改定义、重新求解，旧 case 保留。

## 验收

```powershell
.\.venv\Scripts\python.exe verify\verify_assembly_example.py
.\.venv\Scripts\python.exe verify\verify_assembly_project_roundtrip.py
.\.venv\Scripts\python.exe verify\verify_gui_assembly.py
```

验收脚本把每次输入和求解证据保留在 `verification/`（可用 `RM_SIM_VERIFICATION_ROOT`
指定到别处），不依赖被清理的临时目录。标准算例不代表任意真实机械结构已完成工程验收。
螺栓与销轴简化假设见 `app/docs/connections.md`；从面应力积分是接触传力的近似核对量，
不是直接接触压力输出。
