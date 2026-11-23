"""offscreen Qt 表单配置→真实主窗口求解→真实部件选择与局部云图验收。"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["TEMP"] = os.environ["TMP"] = "E:/rm_sim/temp"
import time
import json
import traceback
import numpy as np
from assembly_check_utils import ROOT, evidence_dir, check_physics


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
    from rm_sim.gui.main_window import MainWindow
    from rm_sim.gui.setup_dialog import SetupDialog
    from rm_sim.gui.result_dialogs import AssemblyResultDialog
    from rm_sim.cad.assembly_import import import_assembly, sync_parts_from_model
    from rm_sim.core.project import Project
    from rm_sim.core.materials import part_material
    from rm_sim.report.plot_aggs import render_field_png

    work = evidence_dir("gui_assembly")
    print(f"证据目录: {work}",flush=True)
    app = QApplication.instance() or QApplication([])
    warnings = []
    QMessageBox.warning = lambda *args, **kwargs: warnings.append(str(args[2:]))
    model = import_assembly(ROOT/"samples/assembly_two_blocks.step")
    win = MainWindow()
    win.project = Project(name="GUI 真实装配",project_dir=str(work))
    sync_parts_from_model(win.project,model)
    win.solid_model = model
    dialog = SetupDialog(win.project,model,parent=win)
    fixed = next(f for f in model.faces if f.solid_tag==model.solids[0].tag and f.normal[1]<-.999)
    top = next(f for f in model.faces if f.solid_tag==model.solids[1].tag and f.normal[1]>.999)
    a = next(f for f in model.faces if f.solid_tag==model.solids[0].tag and f.normal[1]>.999)
    b = next(f for f in model.faces if f.solid_tag==model.solids[1].tag and f.normal[1]<-.999)
    add = next(x for x in dialog.findChildren(QPushButton) if x.text()=="添加定义")
    def choose(combo,pid,tag):
        index = next(i for i in range(combo.count()) if tuple(combo.itemData(i))==(pid,tag))
        combo.setCurrentIndex(index)
    choose(dialog.face_a,"p1",fixed.tag); dialog.kind.setCurrentText("固定"); add.click()
    choose(dialog.face_a,"p2",top.tag); dialog.kind.setCurrentText("面力"); dialog.value.setValue(1000);add.click()
    choose(dialog.face_a,"p1",a.tag); choose(dialog.face_b,"p2",b.tag)
    dialog.kind.setCurrentText("可分离接触与摩擦"); dialog.mu.setValue(.3);add.click()
    # 材料编辑必须只作用于所选部件。
    dialog.material.setCurrentText("6061");dialog.set_material()
    assert part_material(win.project,win.project.parts[0]).name=="6061"
    assert part_material(win.project,win.project.parts[1]).name=="Q235"
    dialog.material.setCurrentText("Q235");dialog.set_material()
    dialog.close()
    assert not warnings, warnings
    assert len(win.project.study.load_cases[0].constraints)==1
    assert len(win.project.connections)==1 and win.project.connections[0].friction_mu==.3
    print("[OK] Qt 表单信号真实写入材料、约束、载荷、连接",flush=True)
    win._on_solve_static()
    deadline = time.monotonic()+900
    while win._last_archive is None and not warnings and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.02)
    assert not warnings, warnings
    assert win._last_archive is not None, "GUI 求解超时或归档未完成"
    assert win._solve_task.state=="completed" and win._act_parts.isEnabled()
    assert set(win._part_views)=={"PART1","PART2"}
    check_physics(win._solve_task.case_dir,1000.)
    parts = {n:{"nodes":len(v.node_ids),"max_u":v.max_u_mag(),"max_mises":v.max_mises()}
             for n,v in win._part_views.items()}
    ctx=json.loads(win._last_archive["results_json"].read_text(encoding="utf-8"))
    interfaces=ctx["assembly"]["interface_pressure"]
    assert {x['side'] for x in interfaces}=={'SA','SB'}
    assert np.linalg.norm(sum((np.asarray(x['force_n']) for x in interfaces),np.zeros(3)))<10
    result_dialog = AssemblyResultDialog(parts,composite={"nodes":len(win._result_fields.node_ids)},parent=win,
                                        interfaces=interfaces)
    result_dialog.part_selected.connect(win.select_result_part)
    recorded = {}
    for name in ("PART1","PART2"):
        result_dialog._combo.setCurrentText(name)
        app.processEvents()
        data = win._active_result
        view = win._part_views[name]
        assert set(data["node_ids"]) == set(view.node_ids)
        assert len(data["points"]) < len(win._result_fields.node_ids)
        png, info = render_field_png(data["points"],data["tris"],np.linalg.norm(data["u"],axis=1),
                                    deform_u=data["u"],deform_scale=100,title=name,unit="mm")
        assert abs(info["max_scalar"]-view.max_u_mag())<1e-12
        (work/f"{name}.png").write_bytes(png)
        recorded[name] = {"nodes":len(view.node_ids),"max_u":info["max_scalar"]}
        assert abs(float(result_dialog._table.item(1,1).text().split()[0])-view.max_u_mag())<1e-8
    assert recorded["PART1"]["max_u"] != recorded["PART2"]["max_u"]
    result_dialog.close();win.close();model.close()
    assert not warnings,warnings
    (work/"verification.json").write_text(json.dumps({"status":"PASS","parts":recorded},indent=2),encoding="utf-8")
    print("VERIFY_GUI_ASSEMBLY: PASS（表单→真实主窗口求解→部件选择→局部标注）",flush=True)


if __name__=="__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
