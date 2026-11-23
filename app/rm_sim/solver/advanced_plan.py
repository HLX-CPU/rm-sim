"""Advanced analysis dependency plans, frozen preparation packages and backend contracts."""
from pathlib import Path
import datetime,hashlib,json,shutil
from .advanced_validation import validate_studies
from .advanced_history import frequency_grid,mechanism_si_payload,evaluate_history
from ..core.advanced import LABELS

VERSION='advanced-plan@1'
STAGES={
 'modal':['resolve_geometry','resolve_materials','assemble_tangent_model','extract_eigenpairs','verify_eigenpairs','archive_modes'],
 'harmonic':['verify_modal_source','assemble_frequency_grid','apply_damping_and_excitation','solve_frequency_response','verify_frequency_response','archive_response'],
 'transient':['resolve_geometry','mesh','map_motion_loads','apply_initial_conditions_and_histories','integrate_transient','verify_time_step_and_energy','archive_response'],
 'mechanism':['resolve_mass_inertia','build_bodies_and_joints','prepare_contacts_and_drives','integrate_mechanism','verify_constraints_and_energy','export_wrench_history','archive_motion'],
 'nonlinear':['resolve_geometry','mesh','verify_constitutive_data','prepare_contact_and_load_steps','integrate_nonlinear','verify_energy_mesh_and_material_sensitivity','archive_response']}
RESULTS={'modal':['frequency_hz','normalized_mode_shapes','eigen_residuals'],
 'harmonic':['frequency_hz','complex_displacement_mm','phase_rad','stress_mpa'],
 'transient':['time_s','displacement_mm','velocity_mm_s','acceleration_mm_s2','stress_mpa','energy_nmm'],
 'mechanism':['time_s','body_poses_mm','joint_coordinates','reaction_force_n','reaction_moment_nmm','energy_nmm'],
 'nonlinear':['time_s','displacement_mm','stress_mpa','equivalent_plastic_strain','damage','contact_force_n','energy_nmm']}


class BackendNotConnected(RuntimeError):pass


class AdvancedBackend:
    """Extension contract: prepare/run/collect must provide real engine evidence.

    engine_connected（模块54 能力位）：仅当 run/collect 已接真实引擎时为 True。
    只注册了 prepare（输入编译）的适配器保持 False——GUI 求解按钮与各门禁
    据此判定引擎可否求解，不能只看适配器是否注册。
    """
    engine_connected: bool = False
    def prepare(self,package):raise BackendNotConnected('该高级分析适配器尚未接通')
    def run(self,prepared,cancel_event,progress):raise BackendNotConnected('该高级分析求解器尚未接通')
    def collect(self,run):raise BackendNotConnected('该高级分析结果解析器尚未接通')


_BACKENDS={}


def register_backend(kind,backend):
    if kind not in STAGES or not isinstance(backend,AdvancedBackend):raise ValueError('后端类型或适配器接口无效')
    if kind in _BACKENDS:raise ValueError('此研究类型已有适配器，不允许静默替换')
    _BACKENDS[kind]=backend


def backend_for(kind):
    if kind not in _BACKENDS:raise BackendNotConnected('此高级研究尚未注册求解适配器：'+kind)
    return _BACKENDS[kind]


def dependencies(study):
    result=list(study.depends_on)
    if study.kind=='harmonic':result.append(study.settings.modal_study_id)
    if study.kind=='transient' and study.settings.load_transfer:result.append(study.settings.load_transfer.mechanism_study_id)
    return sorted(set(result))


def build_plan(project):
    from ..core.store import to_dict
    errors=validate_studies(project)
    if errors:raise ValueError('\n'.join(f"{e['path']}: {e['message']}" for e in errors))
    studies={s.id:s for s in project.advanced_studies if s.enabled}
    if not studies:raise ValueError('请至少启用一个高级研究')
    order=[];done=set()
    def visit(sid):
        if sid in done:return
        for parent in dependencies(studies[sid]):visit(parent)
        done.add(sid);order.append(sid)
    for sid in studies:visit(sid)
    nodes=[];final={}
    for sid in order:
        s=studies[sid];previous=[final[p] for p in dependencies(s)]
        for stage in STAGES[s.kind]:
            nid=sid+':'+stage
            nodes.append(dict(id=nid,study_id=sid,stage=stage,depends_on=list(previous),state='planned'))
            previous=[nid]
        final[sid]=previous[0]
    return dict(version=VERSION,state='configured_not_computed',solver_executed=False,
        configuration_valid=True,geometry_verified=False,numerically_verified=False,study_order=order,nodes=nodes,
        studies=[dict(id=sid,name=studies[sid].name,kind=studies[sid].kind,settings=to_dict(studies[sid].settings),
            engine='Project Chrono' if studies[sid].kind=='mechanism' else 'CalculiX / impact backend to validate',
            adapter_status='not_connected',result_contract=RESULTS[studies[sid].kind]) for sid in order],
        existing_entry_scope='单零件无预应力模态和Chrono最小场景另有旧入口；不是本框架的整装求解路径')


def _json(value):return json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)


def _digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write_preparation_package(project,directory,*,check_cancel=lambda:None):
    """No engine execution. Never overwrite existing preparation packages."""
    from ..core.store import to_dict
    check_cancel();plan=build_plan(project);root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=False)
    manifest=dict(version=VERSION,state='preparing',solver_executed=False,results_available=False,files={})
    manifest_path=root/'manifest.json';manifest_path.write_text(_json(manifest),encoding='utf-8')
    try:
        data=to_dict(project)
        (root/'project_snapshot.json').write_text(_json(data),encoding='utf-8')
        (root/'plan.json').write_text(_json(plan),encoding='utf-8')
        source=Path(project.study.geometry_file) if project.study.geometry_file else None
        if source and source.is_file():
            before=_digest(source);destination=root/('geometry'+source.suffix.lower())
            with source.open('rb') as src,destination.open('xb') as dst:
                for chunk in iter(lambda:src.read(1024**2),b''):check_cancel();dst.write(chunk)
            if _digest(source)!=before or _digest(destination)!=before:raise ValueError('几何在准备期间变化')
            manifest['geometry_snapshot']=destination.name
        previews={}
        for study in project.advanced_studies:
            check_cancel()
            if not study.enabled:continue
            if study.kind=='harmonic':previews[study.id]={'frequency_hz':frequency_grid(study.settings).tolist()}
            if study.kind=='mechanism':previews[study.id]=mechanism_si_payload(study.settings)
            if study.kind in ('transient','nonlinear'):
                import numpy as np
                opt=study.settings;t=np.unique(np.r_[np.linspace(0,opt.time.duration_s,201),[a for a,b in opt.amplitude.samples if 0<=a<=opt.time.duration_s]])
                previews[study.id]={'time_s':t.tolist(),'amplitude':evaluate_history(opt.amplitude,t).tolist(),'scope':'input history preview, not a dynamic response'}
        (root/'adapter_inputs.json').write_text(_json(previews),encoding='utf-8')
        report=['# 高级分析准备包','','状态：配置已校验，尚未计算。没有频率、应力、运动或损伤结果。','',
                '几何挂接、质量惯量、求解器适配及数值验证将在后续阶段完成。','', '| 研究 | 类型 | 后端 |', '|---|---|---|']
        for s in plan['studies']:report.append(f"| {s['name']} | {LABELS[s['kind']]} | 待接通并验证 |")
        report += ['', '依赖顺序：'+', '.join(plan['study_order']),'',
                   '输入单位：结构采用N-mm-s，机构适配输入转换为m-kg-s-N；力矩转换包含作用点平移项。',
                   '归一化模态振型不代表实际振幅；刚体接触载荷不等于材料冲击应力；启用损伤须有标定数据。']
        (root/'preparation_report.md').write_text('\n'.join(report),encoding='utf-8')
        manifest.update(state='prepared_not_computed',created=datetime.datetime.now().isoformat(),
            files={p.name:_digest(p) for p in root.iterdir() if p.is_file() and p.name!='manifest.json'})
        check_cancel();manifest_path.write_text(_json(manifest),encoding='utf-8')
        return root
    except BaseException as exc:
        manifest.update(state='preparation_cancelled' if getattr(exc,'is_cancellation',False) else 'preparation_failed',error=str(exc))
        manifest_path.write_text(_json(manifest),encoding='utf-8');raise


def load_preparation_package(directory):
    from ..core.store import from_dict
    root=Path(directory).resolve();manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version')!=VERSION or manifest.get('state')!='prepared_not_computed':raise ValueError('准备包不完整或版本不支持')
    if manifest.get('solver_executed') is not False or manifest.get('results_available') is not False:raise ValueError('准备包不能声明已计算结果')
    for name,expected in manifest['files'].items():
        path=(root/name).resolve()
        if path.parent!=root or not path.is_file() or _digest(path)!=expected:raise ValueError('准备包文件缺失或校验失败：'+name)
    required={'project_snapshot.json','plan.json','adapter_inputs.json','preparation_report.md'}
    if not required<=set(manifest['files']):raise ValueError('准备包缺少必要清单项')
    project=from_dict(json.loads((root/'project_snapshot.json').read_text(encoding='utf-8')))
    plan=json.loads((root/'plan.json').read_text(encoding='utf-8'))
    if build_plan(project)!=plan:raise ValueError('准备包配置与依赖方案不一致')
    geometry=manifest.get('geometry_snapshot')
    if geometry:
        if geometry not in manifest['files']:raise ValueError('几何快照不在校验清单中')
        project.study.geometry_file=str(root/geometry)
    project.project_dir=str(root)
    return dict(project=project,plan=plan,manifest=manifest)


def _register_default_backends():
    """内建适配器注册（模块54）：modal=输入编译可用（prepare），run/collect
    尚未接引擎（engine_connected=False）。局部导入避免模块级循环；若
    advanced_modal 先被导入（本模块尚未执行完），由其模块尾部补注册。
    双向安全：advanced_modal 尾部也可能已注册（先导入场景），查重后再注册。"""
    try:
        from .advanced_modal import ModalBackend
    except ImportError:
        return
    if 'modal' not in _BACKENDS:
        register_backend('modal', ModalBackend())


_register_default_backends()
