"""动力学最小场景的 SI 单位时间历史图与报告。"""
from pathlib import Path
import numpy as np
from .plot_aggs import matplotlib


def write_chrono_report(result, directory):
    import matplotlib.pyplot as plt
    directory=Path(directory)
    fig,axes=plt.subplots(3,1,figsize=(10,9),constrained_layout=True)
    pendulum=result['pendulum'];spring=result['spring'];collision=result['collision']
    axes[0].plot([x['time_s'] for x in pendulum['samples']],[x['angle_rad'] for x in pendulum['samples']])
    axes[0].set(ylabel='摆角 [rad]',title='转动铰链：均匀杆摆')
    axes[1].plot([x['time_s'] for x in spring['samples']],[x['x_m']-1 for x in spring['samples']],label='伸长 [m]')
    axes[1].set(ylabel='弹簧伸长 [m]',title='棱柱关节与弹簧阻尼质量块')
    axes[2].plot([x['time_s'] for x in collision['samples']],[x['y_m'] for x in collision['samples']],label='箱体质心高度')
    axes[2].axhline(.1,color='gray',linestyle='--',label='接触静止高度')
    axes[2].set(ylabel='高度 [m]',xlabel='时间 [s]',title='刚体下落、地面接触与摩擦（非冲击应力）')
    for ax in axes:ax.grid(alpha=.3);ax.set_xlabel('时间 [s]')
    axes[2].legend()
    png=directory/'chrono_history.png'
    try:fig.savefig(png,dpi=140)
    finally:plt.close(fig)
    report=directory/'report.md'
    text=f'''# Chrono 最小场景报告

- 单位：m、kg、s、N。
- 铰链摆周期：{pendulum['period_s']:.6g} s；小角度刚杆理论 {pendulum['theory_period_s']:.6g} s。
- 恒速电机：设定 2 rad/s，实际 {result['motor']['actual_rad_s']:.6g} rad/s。
- 弹簧质量块 1 s 位置：{spring['x_at_1s_m']:.9g} m；解析解 {spring['theory_x_at_1s_m']:.9g} m。
- 碰撞最大接触数：{collision['max_contacts']}；最终质心高度 {collision['final_y_m']:.6g} m；最终水平速度 {collision['final_vx_m_s']:.6g} m/s。
- 范围：{result['scope']}

![时间历史](chrono_history.png)

原始逐步状态和弹簧力：chrono_results.json。运动载荷到有限元的传递另有 source SHA256、时刻、坐标变换及计算产物，不是双向动力学耦合。
'''
    report.write_text(text,encoding='utf-8')
    return {'report':str(report),'history_png':str(png)}
