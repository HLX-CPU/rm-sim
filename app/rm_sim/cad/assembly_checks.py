"""Exact B-rep gap/interference evidence for assembly poses, without moving CAD."""
from __future__ import annotations

import itertools
import numpy as np

from .. import gmsh_runtime


def _remove_temporary_entities(gmsh, before):
    for dimension in (3, 2, 1, 0):
        remaining = set(gmsh.model.occ.getEntities()) - before
        doomed = [(d, t) for d, t in remaining if d == dimension]
        if doomed:
            gmsh.model.occ.remove(doomed, recursive=False)
    gmsh.model.occ.synchronize()


def check_assembly_geometry(model, translations=None, pairs=None, tolerance_mm=1e-5):
    """Check copied bodies at optional per-body translations; no source edits.

    Interference is measured by OCC solid intersection volume, not bounding-box
    overlap. Zero distance alone is explicitly not classified as load-bearing
    contact or as a kinematic lock. Per-pair failures remain visible.
    """
    import gmsh

    model._ensure_loaded()
    translations = translations or {}
    solids = {s.tag: s for s in model.solids}
    labels = {p.tag: p.name for p in getattr(model, "parts", ())}
    pairs = list(itertools.combinations(solids, 2)) if pairs is None else list(pairs)
    output = []
    with gmsh_runtime.session():
        gmsh.model.setCurrent(model._name)
        for a, b in pairs:
            row = {"a": a, "b": b, "name_a": labels.get(a, str(a)), "name_b": labels.get(b, str(b))}
            before = set(gmsh.model.occ.getEntities())
            try:
                copies = [gmsh.model.occ.copy([(3, tag)])[0] for tag in (a, b)]
                for tag, copied in zip((a, b), copies):
                    gmsh.model.occ.translate([copied], *translations.get(tag, (0., 0., 0.)))
                gmsh.model.occ.synchronize()
                distance = gmsh.model.occ.getDistance(*copies[0], *copies[1])
                if not np.isfinite(distance[0]) or distance[0] < 0:
                    raise RuntimeError("OCC distance calculation failed")
                row.update(distance_mm=float(distance[0]), closest_a=list(distance[1:4]), closest_b=list(distance[4:7]))
                boxes = [gmsh.model.getBoundingBox(d, t) for d, t in copies]
                disjoint = any(boxes[0][i+3] < boxes[1][i] or boxes[1][i+3] < boxes[0][i] for i in range(3))
                if disjoint:
                    volume = 0.
                else:
                    out, _ = gmsh.model.occ.intersect([copies[0]], [copies[1]])
                    volume = sum(gmsh.model.occ.getMass(d, t) for d, t in out if d == 3)
                row["intersection_mm3"] = float(volume)
                volume_tolerance = max(1e-7, min(solids[a].volume_mm3, solids[b].volume_mm3) * 1e-9)
                row["classification"] = ("interference" if volume > volume_tolerance else
                                         "zero_distance" if distance[0] <= tolerance_mm else "separated")
            except Exception as exc:
                row.update(classification="check_failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                # Only transient OCC entities created by this check are removed.
                _remove_temporary_entities(gmsh, before)
            output.append(row)
    return {"translations_mm": {str(k): list(v) for k, v in translations.items()}, "pairs": output,
            "interpretation": "Solid intersection is geometric interference. Zero distance may be point/edge/face contact; it does not prove a lock or a valid structural connection.",
            "checked_pairs": len(output), "interference_count": sum(r["classification"] == "interference" for r in output),
            "failed_pairs": sum(r["classification"] == "check_failed" for r in output)}


def find_contact_faces(model, pairs=None, distance_tolerance_mm=1e-5, min_area_mm2=.01, planar_only=True):
    """Return exact common surface patches as candidates, without making joints.

    A coplanar bounding-box overlap is only a filter. OCC surface intersection
    must have positive area. Curved surfaces are included only when planar_only
    is false. Positive-clearance interfaces are excluded; no joint is inferred.
    """
    import gmsh

    model._ensure_loaded()
    solids = {s.tag: s for s in model.solids}
    pairs = list(itertools.combinations(solids, 2)) if pairs is None else list(pairs)
    labels = {p.tag: p.name for p in getattr(model, 'parts', ())}
    candidates, errors = [], []
    with gmsh_runtime.session():
        gmsh.model.setCurrent(model._name)
        def planar(face):
            if gmsh.model.getType(2,face.tag) == 'Plane':
                return True
            # STEP exporters can encode a reflected plane as a BSpline surface.
            # Check its actual parameter surface, rather than its encoding name.
            lo,hi=gmsh.model.getParametrizationBounds(2,face.tag)
            uv=[float(value) for u in np.linspace(lo[0],hi[0],5) for v in np.linspace(lo[1],hi[1],5) for value in (u,v)]
            points=np.asarray(gmsh.model.getValue(2,face.tag,uv)).reshape(-1,3)
            centered=points-points.mean(axis=0)
            _,s,vh=np.linalg.svd(centered,full_matrices=False)
            return bool(s[1] > distance_tolerance_mm and np.max(np.abs(centered@vh[-1])) <= distance_tolerance_mm*.1)
        planarity = {f.tag:planar(f) for f in model.faces}
        faces = {tag: [f for f in model.faces if f.solid_tag == tag and (not planar_only or planarity[f.tag])]
                 for tag in solids}
        for a, b in pairs:
            for fa in faces[a]:
                na = np.asarray(fa.normal)
                for fb in faces[b]:
                    both_planar = planarity[fa.tag] and planarity[fb.tag]
                    if both_planar:
                        if abs(float(na @ np.asarray(fb.normal))) < 1-1e-7:
                            continue
                        if abs(float((np.asarray(fb.center)-fa.center) @ na)) > distance_tolerance_mm:
                            continue
                    if any(fa.bbox_max[i]+distance_tolerance_mm < fb.bbox_min[i]
                           or fb.bbox_max[i]+distance_tolerance_mm < fa.bbox_min[i] for i in range(3)):
                        continue
                    before = set(gmsh.model.occ.getEntities())
                    try:
                        if not both_planar:
                            distance=gmsh.model.occ.getDistance(2,fa.tag,2,fb.tag)[0]
                            if not np.isfinite(distance) or distance < 0:
                                raise RuntimeError("OCC face distance calculation failed")
                            if distance > distance_tolerance_mm:
                                continue
                        ac = gmsh.model.occ.copy([(2,fa.tag)])
                        bc = gmsh.model.occ.copy([(2,fb.tag)])
                        out, _ = gmsh.model.occ.intersect(ac,bc)
                        patches = [(d,t,gmsh.model.occ.getMass(d,t)) for d,t in out if d == 2]
                        area = sum(v for d,t,v in patches)
                        if area < min_area_mm2:
                            continue
                        centroid = sum((np.asarray(gmsh.model.occ.getCenterOfMass(d,t))*v for d,t,v in patches),np.zeros(3))/area
                        candidates.append({'a':a,'b':b,'name_a':labels.get(a,str(a)),'name_b':labels.get(b,str(b)),
                            'face_a':fa.tag,'face_b':fb.tag,'common_area_mm2':float(area),
                            'common_centroid_mm':centroid.tolist(),'normal_a':list(fa.normal),'normal_b':list(fb.normal),
                            'face_area_a_mm2':fa.area_mm2,'face_area_b_mm2':fb.area_mm2,
                            'geometry_type':'planar' if both_planar else 'curved'})
                    except Exception as exc:
                        errors.append({'a':a,'b':b,'face_a':fa.tag,'face_b':fb.tag,'error':f'{type(exc).__name__}: {exc}'})
                    finally:
                        _remove_temporary_entities(gmsh,before)
    return {'candidates':candidates,'errors':errors,'body_pairs_considered':len(pairs),'planar_only':planar_only,
            'scope':'Exact common surface patches only. These are candidates, not automatically bonded or frictional joints. Positive-clearance interfaces are not covered.'}


def find_planar_contact_faces(model, pairs=None, distance_tolerance_mm=1e-5, min_area_mm2=.01):
    return find_contact_faces(model,pairs,distance_tolerance_mm,min_area_mm2,planar_only=True)
