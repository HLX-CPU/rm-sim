"""No CAD/mesh/solver execution: schemas, dependency graph, units, package integrity and Qt forms."""
import os,json,time,subprocess,sys
from pathlib import Path
from copy import deepcopy
import numpy as np
from assembly_check_utils import ROOT,evidence_dir


def main():
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from rm_sim.core.advanced import (new_study,TimeHistory,RigidBodySpec,JointSpec,SpringDamperSpec,
        ContactPairSpec,PlasticCurve,LoadTransferSpec,ModalSettings,MechanismSettings)
    from rm_sim.core.project import Project,Part,Constraint,TractionLoad,LoadCase
    from rm_sim.core.face_identity import FaceFingerprint
    from rm_sim.core.store import to_dict,from_dict
    from rm_sim.core.project_bundle import save_bundle,open_bundle
    from rm_sim.solver.advanced_validation import validate_studies
    from rm_sim.solver.advanced_history import evaluate_history,transfer_wrench,mechanism_si_payload
    from rm_sim.solver.advanced_plan import build_plan,write_preparation_package,load_preparation_package,AdvancedBackend,BackendNotConnected,backend_for,register_backend
    from rm_sim.solver.advanced_tasks import submit_preparation,PreparationCancelled
    from rm_sim.gui.advanced_dialog import AdvancedWorkbench,ObjectForm,ObjectDialog
    from rm_sim.gui.main_window import MainWindow
    from PySide6.QtWidgets import QApplication,QFileDialog
    from PySide6.QtCore import QCoreApplication,QEvent
    app=QApplication([]);work=evidence_dir('advanced_framework')
    from PySide6.QtGui import QFontDatabase,QFont
    # Windows offscreen does not enumerate system fonts; load one for render QA.
    font_source='C:/Windows/Fonts/msyh.ttc'
    if not QFontDatabase.families():
        font_id=QFontDatabase.addApplicationFont(font_source)
        assert font_id>=0
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0],9))
    original_popen=subprocess.Popen;launches=[]
    def forbidden(*args,**kwargs):launches.append(str(args));raise AssertionError('Framework launched an external process')
    subprocess.Popen=forbidden
    try:
        project=Project(name='Configuration fixture only',project_dir=str(work))
        opaque=work/'opaque_geometry_fixture.step';opaque.write_bytes(b'Configuration test fixture; not a CAD model.\n')
        project.study.geometry_file=str(opaque)
        project.parts=[Part('p1',name='fixed fixture'),Part('p2',name='moving fixture')]
        fp=FaceFingerprint(1.,(0.,0.,0.),(0.,0.,1.))
        project.study.load_cases=[LoadCase(name='fixture excitation',constraints=[Constraint('fixed',face_tag=1,face=fp,part_id='p1')],
            loads=[TractionLoad('force',face_tag=2,face=fp,part_id='p2',total_force_n=1.)])]
        modal=new_study('modal');harmonic=new_study('harmonic');mechanism=new_study('mechanism')
        transient=new_study('transient');nonlinear=new_study('nonlinear')
        harmonic.settings.modal_study_id=modal.id
        mechanism.settings.bodies=[RigidBodySpec('base','p1',fixed=True),
            RigidBodySpec('arm','p2',mass_source='explicit',mass_kg=2.,inertia_kg_mm2=(10.,10.,10.,0.,0.,0.))]
        mechanism.settings.histories=[TimeHistory('drive','rad',[(0.,0.),(1.,1.)])]
        mechanism.settings.joints=[JointSpec('pivot','revolute','base','arm',drive='position',history_id='drive')]
        mechanism.settings.springs=[SpringDamperSpec(body_a='base',body_b='arm',stiffness_n_mm=2.,damping_n_s_mm=.1)]
        mechanism.settings.contacts=[ContactPairSpec(body_b='arm')]
        transient.settings.load_transfer=LoadTransferSpec(mechanism.id,'arm','p2')
        nonlinear.settings.plasticity_enabled=True
        nonlinear.settings.plastic_curves=[PlasticCurve('p2',[(0.,100.),(.1,120.)],'configuration fixture, not material data')]
        project.advanced_studies=[harmonic,transient,nonlinear,modal,mechanism]
        assert not validate_studies(project),validate_studies(project)
        restored=from_dict(to_dict(project));assert to_dict(restored)==to_dict(project)
        save_bundle(work/'project.rmsimproject',project)
        bundled=open_bundle(work/'project.rmsimproject',work/'relocated')['project']
        assert to_dict(bundled.advanced_studies)==to_dict(project.advanced_studies)
        plan=build_plan(project);order=plan['study_order']
        assert order.index(modal.id)<order.index(harmonic.id) and order.index(mechanism.id)<order.index(transient.id)
        assert not plan['solver_executed'] and not plan['geometry_verified'] and all(n['state']=='planned' for n in plan['nodes'])
        bad=deepcopy(project);bad.advanced_studies[3].depends_on=[harmonic.id]
        assert any('循环' in i['message'] for i in validate_studies(bad))
        bad=deepcopy(project);bad.advanced_studies[0].settings.modal_study_id='missing'
        assert validate_studies(bad)
        bad=deepcopy(project);bad.advanced_studies[4].settings.histories[0].unit='mm'
        assert any('单位' in i['message'] for i in validate_studies(bad))
        bad=deepcopy(project);bad.advanced_studies[4].settings.bodies[1].inertia_kg_mm2=(1.,1.,10.,0.,0.,0.)
        assert any('惯量' in i['message'] for i in validate_studies(bad))
        bad=deepcopy(project);bad.advanced_studies[2].settings.damage.enabled=True
        assert any('断裂' in i['message'] or '正数' in i['message'] for i in validate_studies(bad))
        h=TimeHistory(samples=[(0.,0.),(1.,2.)],outside='zero')
        assert np.allclose(evaluate_history(h,[-1,.25,1,2]),[0,.5,2,0])
        h.outside='error'
        try:evaluate_history(h,[-1])
        except ValueError:pass
        else:raise AssertionError('Out-of-range history silently extrapolated')
        rotation=(0.,-1.,0.,1.,0.,0.,0.,0.,1.)
        f,m=transfer_wrench([1,0,0],[0,0,1],rotation,[100,0,0],[0,0,0],[0,0,0])
        assert np.allclose(f,[0,1,0]) and np.allclose(m,[0,0,1100]),(f,m)
        si=mechanism_si_payload(mechanism.settings)
        assert si['springs'][0]['stiffness_n_m']==2000 and si['springs'][0]['damping_n_s_m']==100
        assert np.allclose(si['bodies'][1]['inertia_kg_m2'][:3],[1e-5]*3,rtol=1e-12,atol=0)
        assert si['unresolved_mass_properties']==['base'] and si['bodies'][0]['mass_kg'] is None
        package=write_preparation_package(project,work/'package')
        loaded=load_preparation_package(package);assert not loaded['manifest']['results_available']
        assert Path(loaded['project'].study.geometry_file).read_bytes()==opaque.read_bytes()
        try:write_preparation_package(project,package)
        except FileExistsError:pass
        else:raise AssertionError('Existing preparation package overwritten')
        plan_file=package/'plan.json';original=plan_file.read_bytes();plan_file.write_bytes(original+b' ')
        try:load_preparation_package(package)
        except ValueError:pass
        else:raise AssertionError('Tampered package accepted')
        plan_file.write_bytes(original)
        try:AdvancedBackend().run(None,None,None)
        except BackendNotConnected:pass
        else:raise AssertionError('Unconnected backend reported success')
        # ---- 模块54 B2 修订（评审实读证实：原 :102-104 断言 backend_for('modal')
        # 必抛 BackendNotConnected，与「注册含可用 prepare 的 modal 适配器」直接冲突；
        # 修订为能力接口断言，其余四类未注册断言与无效接口断言保留）；
        # ---- 模块55 B2 续：modal 后端已接入真实 ccx（run/collect 实跑证据由
        # verify_assembly_modal_solve.py 承载——本门禁进程不解算），断言推进一档：
        # engine_connected=True；其余四类永远保持未注册抛 BackendNotConnected 断言。
        modal_adapter=backend_for('modal')
        assert type(modal_adapter).prepare is not AdvancedBackend.prepare,'modal prepare not wired'
        assert modal_adapter.engine_connected is True,'modal engine must be connected (模块55)'
        for _kind in ('harmonic','transient','mechanism','nonlinear'):
            try:backend_for(_kind)
            except BackendNotConnected:pass
            else:raise AssertionError('Missing adapter silently selected a fallback: '+_kind)
        try:register_backend('modal',object())
        except ValueError:pass
        else:raise AssertionError('Invalid adapter interface accepted')
        def cancelled():raise PreparationCancelled('fixture cancellation')
        try:write_preparation_package(project,work/'cancelled',check_cancel=cancelled)
        except PreparationCancelled:pass
        else:raise AssertionError('Preparation ignored cancellation')
        checks=[]
        def cancel_mid_copy():
            checks.append(True)
            if len(checks)==2:raise PreparationCancelled('cancel during opaque input copy')
        try:write_preparation_package(project,work/'cancelled_mid_copy',check_cancel=cancel_mid_copy)
        except PreparationCancelled:pass
        else:raise AssertionError('Copy cancellation was ignored')
        assert json.loads((work/'cancelled_mid_copy/manifest.json').read_text(encoding='utf-8'))['state']=='preparation_cancelled'
        try:load_preparation_package(work/'cancelled_mid_copy')
        except ValueError:pass
        else:raise AssertionError('Cancelled preparation accepted')
        form=ObjectForm(modal.settings,project,modal.settings)
        form.widgets['modes'].setValue(8);assert form.value().modes==8 and modal.settings.modes==12
        mechform=ObjectForm(mechanism.settings,project,mechanism.settings)
        assert to_dict(mechform.value())==to_dict(mechanism.settings)
        dialog=AdvancedWorkbench(project);dialog.kind.setCurrentIndex(dialog.kind.findData('modal'));dialog.add()
        assert len(dialog.project.advanced_studies)==6 and len(project.advanced_studies)==5
        dialog.show();app.processEvents();dialog.grab().save(str(work/'advanced_workbench.png'))
        parameter_dialog=ObjectDialog(mechanism.settings,project,mechanism.settings)
        parameter_dialog.show();app.processEvents();parameter_dialog.grab().save(str(work/'mechanism_parameters.png'))
        parameter_dialog.close();parameter_dialog.deleteLater()
        dialog.reject();dialog.deleteLater();form.deleteLater();mechform.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        # Exercise the real menu and apply a transactional study collection.
        win=MainWindow();win.project=deepcopy(project)
        original_exec=AdvancedWorkbench.exec
        def apply_from_menu(self):self.changed=True;return self.DialogCode.Accepted
        AdvancedWorkbench.exec=apply_from_menu
        try:
            action=next(a for menu in win.menuBar().actions() if menu.menu() for a in menu.menu().actions() if a.text()=='高级分析工作台…')
            action.trigger();assert win.project.dirty
        finally:AdvancedWorkbench.exec=original_exec
        task=submit_preparation(project,work/'background');end=time.monotonic()+10
        old_name=project.advanced_studies[0].name;project.advanced_studies[0].name='changed after submit'
        while task.state in ('pending','preparing') and time.monotonic()<end:app.processEvents();time.sleep(.005)
        assert task.state=='prepared_not_computed' and not task.solver_executed,(task.state,task.error)
        assert load_preparation_package(task.directory)['manifest']['solver_executed'] is False
        assert load_preparation_package(task.directory)['project'].advanced_studies[0].name==old_name
        project.advanced_studies[0].name=old_name
        win.close();win.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        assert not launches and 'gmsh' not in sys.modules and 'pychrono' not in sys.modules
        (work/'verification.json').write_text(json.dumps(dict(studies=5,dependency_graph=True,typed_project_roundtrip=True,
            project_bundle_roundtrip=True,invalid_units_inertia_cycles_damage_rejected=True,
            wrench_force_n=f.tolist(),wrench_moment_nmm=m.tolist(),package=str(package),
            tamper_and_overwrite_rejected=True,backend_not_connected_rejected=True,
            qt_forms_and_menu=True,background_preparation=str(task.directory),solver_processes_launched=launches,
            offscreen_font_source=font_source,
            geometry_or_model_analysis_performed=False),ensure_ascii=False,indent=2),encoding='utf-8')
    finally:subprocess.Popen=original_popen;app.shutdown()
    print('VERIFY_ADVANCED_FRAMEWORK: PASS',work,flush=True)


if __name__=='__main__':main()
