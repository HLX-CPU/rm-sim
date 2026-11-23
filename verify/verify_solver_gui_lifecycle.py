"""Offscreen real child-process cancellation through the window's cancel/close actions."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import json
import time
from assembly_check_utils import evidence_dir
from verify_scheduler_lifecycle import build_probe


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QMessageBox
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.solver.tasks import SolveScheduler
    from rm_sim.solver.process_memory import process_memory
    app=QApplication.instance() or QApplication([])
    work=evidence_dir('solver_gui_lifecycle');binary=build_probe(work)
    warnings=[]
    QMessageBox.warning=lambda *a,**k:warnings.append(a)
    def spin(condition,timeout=10):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            app.processEvents()
            if condition():return
            time.sleep(.01)
        raise AssertionError('GUI lifecycle condition timed out')
    win=MainWindow();win._scheduler=SolveScheduler(binary,max_workers=1)
    def launch():
        task=win._scheduler.submit('spawn','not a solver',work/'cases')
        win._solve_task=task
        win._set_solve_busy(True)
        win._solve_timer=QTimer(win);win._solve_timer.timeout.connect(win._poll_solve_task)
        win._solve_timer.start(20)
        spin(lambda:(task.case_dir/'spawn.leaf.pid').exists())
        return task,int((task.case_dir/'spawn.leaf.pid').read_text())
    task,leaf=launch()
    assert all(not a.isEnabled() for a in win._act_modifying)  # 模块42 B1 修订：busy 时修改类禁用
    assert all(a.isEnabled() for a in win._act_viewing)  # busy 时查看类开放
    win._solve_cancel_button.click()
    spin(lambda:task.state=='cancelled' and win._solve_cancel_button is None)
    spin(lambda:process_memory(leaf) is None)
    assert all(a.isEnabled() for a in win._act_modifying)  # 模块42 B1 修订：结束后修改类恢复
    assert not warnings
    # Closing a computing window requests cancellation and finishes closing once stopped.
    task,leaf=launch();win.close()
    spin(lambda:task.state=='cancelled' and win._solve_cancel_button is None)
    spin(lambda:process_memory(leaf) is None)
    assert win._close_after_solve and not warnings
    (work/'verification.json').write_text(json.dumps({'cancel_button':'pass','close_cancels_child':'pass',
        'unlocked_after_cancel':True,'no_false_failure_popup':True}),encoding='utf-8')
    print('VERIFY_SOLVER_GUI_LIFECYCLE: PASS',work,flush=True)


if __name__=='__main__':main()
