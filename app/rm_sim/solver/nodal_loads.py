"""Reconstruct generated static CLOAD / GRAV / pressure loads for RF correction.

CCX RF is external nodal force = reaction + applied nodal load (manual 7.98).
Only the supported single-step, small-displacement deck subset is reconstructed;
unsupported histories are explicitly reported, never assumed to have zero load.
"""
from __future__ import annotations
import numpy as np

VERSION = 'ccx-static-reaction@1'
_CORNERS = np.array(((0.,0.,0.), (1.,0.,0.), (0.,1.,0.), (0.,0.,1.)))
_FACES = ((0,1,2), (0,1,3), (1,2,3), (0,2,3))
_G, _H = .5854101966249685, .1381966011250105
_TET_POINTS = np.array(((_H,_H,_H), (_G,_H,_H), (_H,_G,_H), (_H,_H,_G)))


class UnsupportedLoad(ValueError):
    pass


def tetra_shape(points, count=10):
    bary = np.column_stack((1.-points.sum(axis=1), points))
    grad = np.array(((-1.,-1.,-1.), (1.,0.,0.), (0.,1.,0.), (0.,0.,1.)))
    if count == 4:
        return bary, np.broadcast_to(grad, (len(points),4,3))
    if count != 10:
        raise UnsupportedLoad(f'不支持{count}节点单元的反力修正')
    shape = np.empty((len(points),10)); dn = np.empty((len(points),10,3))
    shape[:,:4] = bary*(2*bary-1)
    dn[:,:4] = (4*bary[:,:,None]-1)*grad[None,:,:]
    for i,(a,b) in enumerate(((0,1),(1,2),(2,0),(0,3),(1,3),(2,3)),4):
        shape[:,i] = 4*bary[:,a]*bary[:,b]
        dn[:,i] = 4*(bary[:,a,None]*grad[b]+bary[:,b,None]*grad[a])
    return shape, dn


def cards(text):
    header, rows = None, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('**'): continue
        if line.startswith('*'):
            if header is not None: yield header, rows
            header, rows = [s.strip().upper() for s in line.split(',')], []
        else: rows.append(line)
    if header is not None: yield header, rows


def evaluate_nodal_loads(text, mesh, selected_nodes):
    """Return applied loads at selected nodes plus global force/moment (origin=0)."""
    sections = list(cards(text))
    kinds = [header[0] for header,_ in sections]
    forbidden = {'*AMPLITUDE','*TRANSFORM','*TEMPERATURE','*INCLUDE','*DYNAMIC',
                 '*FREQUENCY','*BUCKLE','*DSLOAD','*INITIAL CONDITIONS'}
    bad = forbidden.intersection(kinds)
    if bad or kinds.count('*STEP') != 1 or '*STATIC' not in kinds:
        raise UnsupportedLoad('反力修正只支持单个小变形静力步；未支持：'+', '.join(sorted(bad)))
    if any(h[0]=='*STEP' and any(s.startswith('NLGEOM') and s!='NLGEOM=NO' for s in h[1:])
           for h,_ in sections):
        raise UnsupportedLoad('大变形载荷需使用当前构形，不能按初始坐标修正')
    nsets = {k.upper():v for k,v in mesh['nsets'].items()}
    elsets = {k.upper():v for k,v in mesh['elsets'].items()}
    nodes, elems = mesh['nodes'], mesh['elems']
    def expand(value, sets, items):
        key = value.upper()
        if key in sets: return sets[key]
        try: tag = int(value)
        except ValueError: raise UnsupportedLoad('找不到节点/单元集合：'+value) from None
        if tag not in items: raise UnsupportedLoad('未知节点/单元：'+value)
        return [tag]
    densities, sections_of, current = {}, [], None
    for header, rows in sections:
        params = dict(p.split('=',1) for p in header[1:] if '=' in p)
        if header[0]=='*MATERIAL': current = params.get('NAME')
        elif header[0]=='*DENSITY':
            if len(rows)!=1 or len(rows[0].split(','))!=1 or not current:
                raise UnsupportedLoad('温度/场变量相关密度尚未支持')
            densities[current] = float(rows[0])
        elif header[0]=='*SOLID SECTION':
            sections_of.append((params.get('ELSET',''),params.get('MATERIAL','')))
    rho_of = {}
    for name, mat in sections_of:
        if mat in densities:
            for eid in expand(name, elsets, elems): rho_of[eid] = densities[mat]
    selected = {int(n):np.zeros(3) for n in selected_nodes}
    total, moment = np.zeros(3), np.zeros(3)
    def collect(conn, xyz, loads):
        nonlocal total, moment
        if not np.isfinite(loads).all(): raise UnsupportedLoad('等效节点载荷含非有限值')
        total += loads.sum(axis=(0,1))
        moment += np.cross(xyz,loads).sum(axis=(0,1))
        for row, values in zip(conn, loads):
            for nid, force in zip(row, values):
                if nid in selected: selected[nid] += force
    # Triangular quadrature integrates quadratic-face pressure consistently,
    # including midside nodes and signed vector area (not a uniform node split).
    x,w = np.polynomial.legendre.leggauss(3); x,w = (x+1)/2,w/2
    uv = np.array([(a,(1-a)*b) for a in x for b in x])
    tw = np.array([wa*wb*(1-a) for a,wa in zip(x,w) for wb in w])
    seen_dload = set()
    for header, rows in sections:
        kind = header[0]
        if kind not in ('*CLOAD','*DLOAD'): continue
        if any(p not in ('OP=NEW','OP=MOD') for p in header[1:]):
            raise UnsupportedLoad('未支持的载荷参数：'+', '.join(header))
        for row in rows:
            parts = [s.strip() for s in row.split(',')]
            if kind=='*CLOAD':
                if len(parts)!=3: raise UnsupportedLoad('CLOAD列数不受支持')
                dof, value = int(parts[1]), float(parts[2])
                if not 1<=dof<=3: raise UnsupportedLoad('仅支持三向平动节点力')
                force = np.zeros(3); force[dof-1] = value
                for nid in expand(parts[0], nsets, nodes):
                    collect([[nid]],np.asarray([[nodes[nid]]]),force.reshape(1,1,3))
                continue
            eids = expand(parts[0], elsets, elems)
            label = parts[1].upper()
            if label != 'GRAV' and label not in ('P1','P2','P3','P4'):
                raise UnsupportedLoad('尚未支持的DLOAD类型：'+label)
            for eid in eids:
                key = (eid,label)
                if key in seen_dload: raise UnsupportedLoad('同一单元重复DLOAD需核对替换/叠加语义')
                seen_dload.add(key)
            if label=='GRAV':
                if len(parts)!=6: raise UnsupportedLoad('GRAV列数不受支持')
                gravity = np.asarray([float(v) for v in parts[3:]])
                norm = np.linalg.norm(gravity)
                if not np.isfinite(norm) or norm==0: raise UnsupportedLoad('重力方向无效')
                gravity *= float(parts[2])/norm
                for count in (4,10):
                    ids = [eid for eid in eids if len(elems[eid])==count]
                    points = _TET_POINTS if count==10 else np.array([[.25,.25,.25]])
                    weights = np.full(len(points),1/(6*len(points)))
                    shape,dn = tetra_shape(points,count)
                    for start in range(0,len(ids),512):
                        batch = ids[start:start+512]; conn = [elems[e] for e in batch]
                        xyz = np.asarray([[nodes[n] for n in r] for r in conn])
                        det = np.linalg.det(np.einsum('qai,eaj->eqij',dn,xyz))
                        if (det<=0).any(): raise UnsupportedLoad('体积积分点存在非正Jacobian')
                        if any(e not in rho_of for e in batch): raise UnsupportedLoad('重力单元没有明确密度')
                        rho = np.asarray([rho_of[e] for e in batch])
                        if not np.isfinite(rho).all() or (rho<0).any(): raise UnsupportedLoad('密度无效')
                        integrals = np.einsum('qa,eq,q->ea',shape,det,weights)
                        collect(conn,xyz,integrals[...,None]*rho[:,None,None]*gravity)
                if any(len(elems[e]) not in (4,10) for e in eids):
                    raise UnsupportedLoad('重力积分只支持四面体')
            else:
                if len(parts)!=3: raise UnsupportedLoad('非均匀压力尚未支持')
                face = _FACES[int(label[1:])-1]
                a,b,c = _CORNERS[list(face)]; basis = np.column_stack((b-a,c-a))
                q = a + uv @ basis.T
                other = next(i for i in range(4) if i not in face)
                sign = 1. if np.dot(np.cross(b-a,c-a),_CORNERS[other]-a)>0 else -1.
                pressure = float(parts[2])
                for count in (4,10):
                    ids = [e for e in eids if len(elems[e])==count]
                    shape,dn = tetra_shape(q,count)
                    face_dn = np.einsum('qai,ij->qaj',dn,basis)
                    for start in range(0,len(ids),512):
                        conn = [elems[e] for e in ids[start:start+512]]
                        xyz = np.asarray([[nodes[n] for n in r] for r in conn])
                        tangent = np.einsum('qai,eaj->eqij',face_dn,xyz)
                        inward_area = sign*np.cross(tangent[:,:,0],tangent[:,:,1])
                        loads = pressure*np.einsum('qa,eqj,q->eaj',shape,inward_area,tw)
                        collect(conn,xyz,loads)
                if any(len(elems[e]) not in (4,10) for e in eids):
                    raise UnsupportedLoad('压力积分只支持四面体')
    fixed_sum = sum(selected.values(),np.zeros(3))
    fixed_moment = sum((np.cross(nodes[n],f) for n,f in selected.items()),np.zeros(3))
    return {'nodes':selected,'version':VERSION,'supported':True,'quantity':'reaction',
            'applied_total_n':total.tolist(),'applied_moment_origin_nmm':moment.tolist(),
            'applied_at_fixed_n':fixed_sum.tolist(),'applied_moment_at_fixed_nmm':fixed_moment.tolist(),
            'integration':'C3D10四点体积/二次三角面九点；CLOAD按INP逐节点展开'}
