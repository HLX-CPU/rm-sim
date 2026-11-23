"""Read-only CAD target discovery and fingerprint resolution for refinement."""
import numpy as np
from .. import gmsh_runtime
from ..core.mesh_controls import EdgeFingerprint
from ..core.face_identity import resolve_face


def targets_for_part(project,model,part_id):
    model._ensure_loaded()
    if part_id:
        part=project.part_by_id(part_id)
        solid=model.solids[part.solid_index]
    elif len(model.solids)==1:
        solid=model.solids[0]
    else:
        raise ValueError('装配局部网格必须指定部件')
    faces=[f for f in model.faces if f.solid_tag==solid.tag]
    with gmsh_runtime.session():
        import gmsh
        gmsh.model.setCurrent(model._name)
        edges=sorted({tag for dim,tag in gmsh.model.getBoundary(
            [(2,f.tag) for f in faces],combined=False,oriented=False) if dim==1})
        info=[]
        for tag in edges:
            # Distance-field sampling inflates OCC tolerance boxes in Gmsh 4.15.
            # Parametric curve coordinates remain stable; do not persist those boxes.
            lo,hi=gmsh.model.getParametrizationBounds(1,tag)
            samples=np.asarray(gmsh.model.getValue(1,tag,np.linspace(lo[0],hi[0],33).tolist())).reshape(-1,3)
            bbox=tuple(np.r_[samples.min(axis=0),samples.max(axis=0)])
            fp=EdgeFingerprint(float(gmsh.model.occ.getMass(1,tag)),
                tuple(gmsh.model.occ.getCenterOfMass(1,tag)),bbox,
                gmsh.model.getType(1,tag))
            info.append((tag,fp))
    return int(solid.tag),faces,info


def resolve_controls(project,model,global_size):
    """Resolve within the matched owning solid; reject missing/ambiguous targets."""
    diag=max(float(np.linalg.norm(np.subtract(model.overall_bbox[1],model.overall_bbox[0]))),1.)
    cache={}; output=[]
    for control in project.study.mesh_controls:
        if not control.enabled:continue
        control.validate(global_size)
        if control.part_id not in cache:
            cache[control.part_id]=targets_for_part(project,model,control.part_id)
        solid,faces,edges=cache[control.part_id]
        if control.kind=='part':dim,tag=3,solid
        elif control.kind=='face':
            tag,status=resolve_face(faces,control.face,diag)
            if status!='ok':raise ValueError(f'「{control.name}」局部网格面{status}，需重新确认')
            dim=2
        else:
            fp=control.edge
            hits=[tag for tag,other in edges if other.curve_type==fp.curve_type
                  and abs(other.length_mm-fp.length_mm)<=1e-6*max(fp.length_mm,1.)
                  and np.allclose(other.center,fp.center,rtol=0,atol=1e-6*diag)
                  and np.allclose(other.bbox,fp.bbox,rtol=0,atol=1e-6*diag)]
            if len(hits)!=1:raise ValueError(f'「{control.name}」局部网格边匹配{len(hits)}个目标，需重新确认')
            dim,tag=1,hits[0]
        output.append(dict(name=control.name,kind=control.kind,part_id=control.part_id,
            dim=dim,tag=int(tag),solid_tag=solid,size_mm=control.size_mm,transition_mm=control.transition_mm))
    return output
