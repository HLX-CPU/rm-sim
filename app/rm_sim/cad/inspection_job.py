"""Standalone geometry inspection worker, isolated from the GUI's OCC runtime."""
from pathlib import Path
from dataclasses import replace
import json
import hashlib
import sys

from .assembly_import import import_assembly
from .assembly_checks import check_assembly_geometry,find_contact_faces


def run(spec_path):
    spec=json.loads(Path(spec_path).read_text(encoding='utf-8'))
    source=Path(spec['step']);before=hashlib.sha256(source.read_bytes()).hexdigest()
    model=import_assembly(source)
    try:
        labels=spec.get('part_names',{})
        model.parts=tuple(replace(p,name=labels.get(str(p.tag),p.name)) for p in model.parts)
        print('Checking assembly gaps and solid intersections',flush=True)
        geometry=check_assembly_geometry(model)
        print('Finding exact common contact patches',flush=True)
        faces=find_contact_faces(model,planar_only=spec.get('planar_only',True))
        result={'step':str(source),'step_sha256':before,'geometry':geometry,'contact_faces':faces,
                'status':'incomplete' if geometry['failed_pairs'] or faces['errors'] else 'completed',
                'scope':'Geometric inspection only. Zero distance or a common patch does not define a mechanical joint.'}
        if hashlib.sha256(source.read_bytes()).hexdigest()!=before:
            raise ValueError('STEP changed during inspection')
        output=Path(spec['output'])
        with output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)
        print(f"INSPECTION_COMPLETED: {result['status']}",flush=True)
        return result
    finally:model.close()


if __name__=='__main__':run(sys.argv[1])
