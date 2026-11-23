"""归一化振型选择与动画帧的 offscreen Qt 验证。"""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from assembly_check_utils import ROOT,evidence_dir


def main():
    from PySide6.QtWidgets import QApplication
    from rm_sim.solver.modal import beam_modal
    from rm_sim.gui.viewport import ViewportHost
    from rm_sim.gui.modal_dialog import ModalResultDialog
    work=evidence_dir('modal_view')
    result=beam_modal(work,ROOT/'samples/beam_100x10x10.step',3)
    app=QApplication.instance() or QApplication([])
    viewport=ViewportHost();dialog=ModalResultDialog(result,viewport)
    first=viewport._canvas.last_png_bytes
    dialog.combo.setCurrentIndex(2);app.processEvents()
    selected=viewport._canvas.last_png_bytes
    assert first and selected and first!=selected
    dialog.toggle();dialog.phase=0.;dialog.render();zero=viewport._canvas.last_png_bytes
    dialog.phase=1.57079632679;dialog.render();peak=viewport._canvas.last_png_bytes
    assert zero!=peak
    (work/'mode1.png').write_bytes(first);(work/'mode3.png').write_bytes(selected)
    (work/'animation_zero.png').write_bytes(zero);(work/'animation_peak.png').write_bytes(peak)
    dialog.close();assert not dialog.timer.isActive();viewport.close()
    print('VERIFY_MODAL_VIEW: PASS 阶次选择、归一化帧差异、关闭后停止动画',flush=True)


if __name__=='__main__':main()
