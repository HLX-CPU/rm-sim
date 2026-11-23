# 模块24：Chrono最小动力学场景

采用[Project Chrono官方开源库](https://github.com/projectchrono/chrono)的PyChrono10.0.0，
按[官方安装指南](https://api.chrono.projectchrono.org/pychrono_installation.html)使用独立Python3.12
环境 `E:/rm_sim/chrono-env`，不把 PyPI 占位包当作引擎。主界面/有限元仍使用原Python3.13环境。
Micromamba程序及软件包归档放D盘；Python环境和依赖缓存放E盘。

初次Micromamba直连下载长时间未取得数据，已停止本轮自行启动的安装进程；按同一份官方解析
计划用Python下载136个包并逐包核对SHA256，再由Micromamba离线链接安装。未结束旧的求解探针。
计划、下载校验记录保留在 `E:/rm_sim/chrono-install-plan.json` 与
`E:/rm_sim/chrono-download-verification.json`。

`chrono_scene.py`真实运行：转动铰链杆摆、恒速电机、棱柱关节弹簧阻尼质量块、
带初速度的箱体落地/碰撞/摩擦。单位m-kg-s-N，保留逐步时序。
验收含摆周期理论（实测1.639s）、半径约束、2rad/s电机、弹簧解析解与链力、
接触数/不穿地/最终滑动停止。GUI「运动→运行Chrono最小场景」提供图表和明确结果目录。

这是一组已验证的最小刚体场景，不是通用机构编辑器，也不输出冲击应力或材料塑性。
