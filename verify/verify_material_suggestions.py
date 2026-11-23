"""Bulk recommendations must preserve measured material and leave unknowns unresolved."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from assembly_check_utils import evidence_dir


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QTableWidget
    from rm_sim.core.project import Part, Material
    from rm_sim.core.materials import suggest_material
    from rm_sim.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    reference = Material(name='6061', yield_strength_mpa=None)
    confirmed = Part('p1', name='NEW快拆', material_name='6061', material_snapshot=reference)
    unknown = Part('p2', name='没有材料牌号的部件', material_confirmed=False)
    explicit = Part('p3', name='6061安装板', material_confirmed=False)
    composite = Part('p4', name='碳纤维轴', material_confirmed=False)
    assert suggest_material(unknown)['name'] is None
    assert suggest_material(explicit)['name'] == '6061'
    assert suggest_material(composite)['name'] is None
    win.project.parts = [confirmed, unknown, explicit, composite]
    def accept():
        dialog = next(w for w in app.topLevelWidgets() if isinstance(w,QDialog) and w.isVisible())
        table = dialog.findChild(QTableWidget)
        assert table.cellWidget(0,1).currentData() is None
        assert table.cellWidget(1,1).currentData() is None
        assert table.cellWidget(2,1).currentData() == '6061'
        assert table.cellWidget(3,1).currentData() is None
        dialog.accept()
    QTimer.singleShot(50,accept)
    win._on_auto_materials()
    assert confirmed.material_snapshot is reference and reference.yield_strength_mpa is None
    assert not unknown.material_confirmed and not composite.material_confirmed
    assert explicit.material_confirmed and explicit.material_name == '6061'
    win.close()
    work = evidence_dir('material_suggestions')
    (work/'verification.txt').write_text('Confirmed snapshot preserved; 6061 matches 6061; unknown and CFRP unresolved.\n',encoding='utf-8')
    print('VERIFY_MATERIAL_SUGGESTIONS: PASS',work,flush=True)


if __name__ == '__main__':
    main()
