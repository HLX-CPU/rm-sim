"""Numeric preparation artifacts exchanged between isolated workflow stages."""
from dataclasses import asdict
import json
import time
from pathlib import Path
import numpy as np


def write_json(path, data):
    path=Path(path)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    for attempt in range(20):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt==19:raise
            time.sleep(.01)


def save_prepared(directory, prepared, project):
    from ..core.store import to_dict
    root=Path(directory)
    result=prepared['mesh_result']
    arrays={'points':result.mesh.points,'tetra10':result.mesh.cells['tetra10'],'node_tags':result.node_tags}
    for prefix,mapping in (('ns',result.node_sets),('es',result.element_sets)):
        for index, ids in enumerate(mapping.values()): arrays[f'{prefix}_{index}']=np.asarray(ids,dtype=np.int64)
    np.savez(root/'prepared.npz',**arrays)
    (root/'deck.inp').write_text(prepared['deck'],encoding='utf-8')
    data={'project':to_dict(project),'stats':asdict(result.stats),'nset_faces':prepared['nset_faces'],
          'fixed_sets':prepared['fixed_sets'],'assembly':prepared['assembly'],
          'node_sets':list(result.node_sets),'element_sets':list(result.element_sets),
          'quality':result.quality}
    write_json(root/'prepared.json',data)


def load_prepared(directory, *, parse_mesh=True):
    from ..core.store import from_dict
    from ..mesh.mesher import MeshResult,MeshStats
    from ..mesh.mesh_data import MeshData
    from .deck import parse_mesh_inp
    root=Path(directory)
    data=json.loads((root/'prepared.json').read_text(encoding='utf-8'))
    with np.load(root/'prepared.npz',allow_pickle=False) as arrays:
        result=MeshResult(mesh=MeshData(points=arrays['points'],cells={'tetra10':arrays['tetra10']}),
            stats=MeshStats(**data['stats']),node_tags=arrays['node_tags'],inp_path=root/'mesh.inp',
            quality=data.get('quality',{}),
            node_sets={name:tuple(int(v) for v in arrays[f'ns_{i}']) for i,name in enumerate(data['node_sets'])},
            element_sets={name:tuple(int(v) for v in arrays[f'es_{i}']) for i,name in enumerate(data['element_sets'])})
    prepared={'mesh_result':result,'nset_faces':data['nset_faces'],'fixed_sets':data['fixed_sets'],
              'assembly':data['assembly'],'deck_path':root/'deck.inp'}
    if parse_mesh: prepared['mesh']=parse_mesh_inp((root/'mesh.inp').read_text(encoding='utf-8'))
    return from_dict(data['project']),prepared
