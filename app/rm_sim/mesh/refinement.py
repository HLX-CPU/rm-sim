"""Gmsh background fields and bounded quality records (no GUI/CAD dependencies)."""
from contextlib import contextmanager
import numpy as np


@contextmanager
def size_fields(gmsh,controls,global_size):
    made=[]; options={}
    def field(kind):
        tag=gmsh.model.mesh.field.add(kind);made.append(tag);return tag
    try:
        if controls:
            for name in ('Mesh.MeshSizeFromPoints','Mesh.MeshSizeExtendFromBoundary'):
                options[name]=gmsh.option.getNumber(name);gmsh.option.setNumber(name,0)
            fields=[]
            for c in controls:
                if c['dim']==3:
                    source=field('MathEval')
                    gmsh.model.mesh.field.setString(source,'F',str(c['size_mm']))
                else:
                    distance=field('Distance')
                    gmsh.model.mesh.field.setNumbers(distance,'SurfacesList' if c['dim']==2 else 'CurvesList',[c['tag']])
                    gmsh.model.mesh.field.setNumber(distance,'Sampling',50)
                    source=field('Threshold')
                    for name,value in (('InField',distance),('SizeMin',c['size_mm']),('SizeMax',global_size),
                                       ('DistMin',0),('DistMax',c['transition_mm'])):
                        gmsh.model.mesh.field.setNumber(source,name,value)
                restricted=field('Restrict')
                gmsh.model.mesh.field.setNumber(restricted,'InField',source)
                gmsh.model.mesh.field.setNumbers(restricted,'VolumesList',[c['solid_tag']])
                fields.append(restricted)
            combined=field('Min');gmsh.model.mesh.field.setNumbers(combined,'FieldsList',fields)
            gmsh.model.mesh.field.setAsBackgroundMesh(combined)
        yield
    finally:
        for tag in reversed(made):gmsh.model.mesh.field.remove(tag)
        for name,value in options.items():gmsh.option.setNumber(name,value)


def quality_record(tags,conn,xyz,sicn,jac,element_sets,recipe):
    if not np.isfinite(sicn).all() or not np.isfinite(jac).all():
        raise ValueError('网格质量含非有限值，不能作为有效网格')
    owners={int(e):name for name,ids in element_sets.items() for e in ids}
    worst=[]
    for i in np.argsort(sicn)[:100]:
        # conn uses mesh row indices; tags retain the exact exported INP IDs.
        worst.append(dict(element=int(tags[i]),part=owners.get(int(tags[i]),'零件'),
            sicn=float(sicn[i]),min_det_jac=float(jac[i]),
            center_mm=xyz[conn[i,:4]].mean(axis=0).tolist()))
    return dict(version='mesh-quality@1',recipe=recipe,element_count=len(tags),
        sicn_percentiles={str(p):float(np.percentile(sicn,p)) for p in (0,1,5,50,95,99,100)},
        below_sicn={str(limit):int((sicn<limit).sum()) for limit in (0,.01,.05,.1)},
        nonpositive_jacobian_count=int((jac<=0).sum()),min_det_jac=float(jac.min()),
        worst_elements=worst,worst_elements_limit=100,
        center_note='四个角点的平均坐标，用于定位；不是二次体积质心')
