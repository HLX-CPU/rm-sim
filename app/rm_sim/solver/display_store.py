"""Numeric display packages; raw solver validation happens in the collector process."""
import hashlib
import json
from pathlib import Path
import numpy as np
from .prepared_store import write_json


def save_display(root,completed,prepared,progress=None):
    from .fields import field_surface
    root=Path(root);fields=completed['fields']
    arrays={name:getattr(fields,name) for name in ('node_ids','u','s_mises','rf','rf_sum')}
    rf_nodes=sorted(fields.rf_table)
    arrays['rf_nodes']=np.asarray(rf_nodes,dtype=np.int64)
    arrays['rf_values']=np.asarray([fields.rf_table[n] for n in rf_nodes],dtype=float).reshape(-1,3)
    arrays['raw_rf_values']=np.asarray([fields.raw_rf_table.get(n,fields.rf_table[n]) for n in rf_nodes],dtype=float).reshape(-1,3)
    views=completed['views']
    for i,view in enumerate(views.values()):
        for key in ('node_ids','u','s_mises','rf'):arrays[f'view{i}_{key}']=getattr(view,key)
    surfaces=['装配合成',*views]
    for i,name in enumerate(surfaces):
        if progress:progress('preparing_display')
        surface=field_surface(prepared['mesh'],fields,None if i==0 else prepared['mesh']['elsets'][name])
        for key,value in surface.items():arrays[f'surface{i}_{key}']=value
    np.savez(root/'display.npz',**arrays)
    with (root/'display.npz').open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    context=json.loads(completed['archive']['results_json'].read_text(encoding='utf-8'))
    write_json(root/'display.json',{'views':list(views),'surfaces':surfaces,'source':fields.source,
        'context':context,'archive':{k:str(v) for k,v in completed['archive'].items()},
        'case_name':completed['summary']['case_name'],'sha256':digest})


def load_display(root,check_cancel=lambda:None):
    from .fields import ResultFields,PartFieldView
    root=Path(root)
    meta=json.loads((root/'display.json').read_text(encoding='utf-8'))
    with (root/'display.npz').open('rb') as stream:
        digest=hashlib.sha256()
        for chunk in iter(lambda:stream.read(2*1024**2),b''):
            check_cancel();digest.update(chunk)
        if digest.hexdigest()!=meta['sha256']:
            raise ValueError('结果显示数据包校验失败，原始计算记录保留')
    with np.load(root/'display.npz',allow_pickle=False) as arrays:
        def read(key):
            check_cancel()
            return arrays[key]
        fields=ResultFields(**{name:read(name) for name in ('node_ids','u','s_mises','rf','rf_sum')},
            rf_table={int(n):v.tolist() for n,v in zip(read('rf_nodes'),read('rf_values'))},source=meta['source'],
            raw_rf_table={int(n):v.tolist() for n,v in zip(read('rf_nodes'),
                read('raw_rf_values') if 'raw_rf_values' in arrays else read('rf_values'))})
        if not all(np.isfinite(a).all() for a in (fields.u,fields.s_mises,fields.rf,fields.rf_sum)):
            raise ValueError('结果包含非有限值，不能显示为有效结果')
        expected=meta['context']['results']
        if (not np.isclose(fields.max_u_mag(),expected['max_u_mag_mm'],rtol=1e-5,atol=1e-9)
                or not np.isclose(float(fields.s_mises.max()),expected['max_mises_mpa'],rtol=1e-5,atol=1e-8)
                or not np.allclose(fields.rf_sum,expected['rf_sum_n'],rtol=1e-5,atol=1e-7)):
            raise ValueError('结果显示包与计算摘要不符')
        views={name:PartFieldView(part=name,**{key:read(f'view{i}_{key}') for key in ('node_ids','u','s_mises','rf')})
               for i,name in enumerate(meta['views'])}
        surfaces={name:{key:read(f'surface{i}_{key}') for key in ('node_ids','points','tris','u','s_mises')}
                  for i,name in enumerate(meta['surfaces'])}
    return {'fields':fields,'views':views,'surfaces':surfaces,'context':meta['context'],
            'archive':{k:Path(v) for k,v in meta['archive'].items()},'case_name':meta['case_name']}
