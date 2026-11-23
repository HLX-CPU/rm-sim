"""Read CalculiX's missed-tie node sets without changing solver artifacts.

Warnings identify reported misses, not a proof that a node is globally floating:
the same node may participate in more than one tie. Absence of a warning file
is also not a structural acceptance result.
"""
from pathlib import Path
import hashlib
import re

_TET10_FACES = ((0,1,2,4,5,6), (0,1,3,4,8,7), (1,2,3,5,9,8), (0,2,3,6,9,7))


def _bounded_log_lines(path):
    """Read even a multi-megabyte single line with bounded memory; keep its prefix."""
    pending = b''
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            pieces = chunk.split(b'\n')
            for piece in pieces[:-1]:
                yield (pending + piece[:2048])[:2048].decode('utf-8', errors='replace')
                pending = b''
            pending = (pending + pieces[-1][:2048])[:2048]
        if pending:
            yield pending.decode('utf-8', errors='replace')


def collect_solver_errors(path):
    """CCX may print ERROR, repair a coefficient, and still say Job finished.

    Keep a small evidence sample and the full log hash; a finish marker must not
    turn such a case into an accepted numerical result.
    """
    path = Path(path)
    result = {'file': path.name, 'count': 0, 'samples': []}
    if not path.exists():
        return result
    remaining = 0
    for number, line in enumerate(_bounded_log_lines(path), start=1):
        if re.match(r'^\s*\*ERROR\b', line, re.IGNORECASE):
            result['count'] += 1
            remaining = 0
            if len(result['samples']) < 16:
                result['samples'].append({'line': number, 'message': line.strip(), 'context': []})
                remaining = 2
        elif remaining:
            result['samples'][-1]['context'].append(line.strip())
            remaining -= 1
    with path.open('rb') as stream:
        result['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def tie_warning_kinds(path):
    """把 gentiedmpc 的警告分成「真的没绑上」和「DOF 已被约束」两类。

    CalculiX 对三种情况都写 WarnNodeMissTiedContact：找不到反向主面
    （no_face）、主面太远（too_far）、以及节点 DOF 已由 SPC 定死而跳过
    （dof_inactive）。前两种是覆盖/间距问题；第三种是求解器正常行为——
    该节点本来就已经被约束，绑定约束在它上面是冗余的。整装实测：3496 个
    警告里 18 个属于第三类。混在一起报会让用户以为有 3496 处断连。
    """
    result = {'no_face': set(), 'too_far': set(), 'dof_inactive': set()}
    window: list[str] = []
    for line in _bounded_log_lines(path):
        text = line.strip()
        if not text:
            continue
        window.append(text)
        if len(window) > 4:
            window.pop(0)
        node = None
        for item in reversed(window[:-1]):
            match = re.search(r'generated for node\s+(\d+)', item)
            if match:
                node = int(match.group(1))
                break
        match = re.match(r'DOF\s+\d+\s+of node\s+(\d+)\s+is not active', text)
        if match:
            result['dof_inactive'].add(int(match.group(1)))
        elif text.startswith('found;') and 'in-face tolerance' in text and node:
            result['no_face'].add(node)
        elif text.startswith('too far away') and node is not None:
            result['too_far'].add(node)
    return result


def diagnostic_message(diagnostics):
    messages = []
    missed = diagnostics.get('untied_nodes', diagnostics.get('reported_missed_nodes', 0))
    if missed:
        kinds = diagnostics.get('warning_kinds') or {}
        detail = '、'.join(
            f'{label} {kinds[key]}'
            for key, label in (('no_face', '找不到反向主面'), ('too_far', '主面太远'))
            if kinds.get(key))
        messages.append(f'求解器报告 {missed} 个不同编号的从面节点未绑定'
                        + (f'（{detail}）' if detail else '') + '。')
    skipped = diagnostics.get('dof_inactive_nodes', 0)
    if skipped:
        messages.append(f'另有 {skipped} 个节点因自由度已被固定约束而跳过绑定（求解器正常行为）。')
    count = diagnostics.get('solver_errors', {}).get('count', 0)
    if count:
        messages.append(f'求解日志含 {count} 条 ERROR，当前结果不接受。')
    if diagnostics.get('read_errors'):
        messages.append('部分诊断文件读取失败，检查尚未完成。')
    return '\n'.join(messages)


def _read_node_set(path):
    nodes = set()
    active = False
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line or line.startswith('**'):
            continue
        if line.startswith('*'):
            active = line.upper().split(',')[0].strip() == '*NSET'
            if active and 'GENERATE' in line.upper():
                raise ValueError('Generated warning node sets require manual inspection')
            continue
        if active:
            for value in line.split(','):
                if value.strip():
                    node = int(value)
                    if node <= 0:
                        raise ValueError('Warning file has a nonpositive node number')
                    nodes.add(node)
    return nodes


def collect_tie_diagnostics(case_dir, job_name, *, mesh=None, connections=None):
    directory = Path(case_dir)
    files, errors, missed = [], [], set()
    for path in sorted(directory.iterdir()):
        if (not path.is_file() or not path.name.startswith(job_name+'_WarnNode')
                or path.suffix.lower() != '.nam'
                or not any(word in path.name for word in ('TiedContact','MasterIntersect','CyclicSymmetry'))):
            continue
        try:
            nodes = _read_node_set(path)
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream,'sha256').hexdigest()
            files.append({'file':path.name,'sha256':digest,'reported_nodes':len(nodes)})
            missed.update(nodes)
        except (OSError,UnicodeError,ValueError) as exc:
            errors.append({'file':path.name,'error':str(exc)})
    try:
        kinds = tie_warning_kinds(directory / (job_name + '.log'))
    except OSError:
        kinds = None
    # 无法分类时按「全部都是未绑定」处理：宁可多报，不能悄悄放行。
    classified = bool(kinds) and bool(kinds['no_face'] or kinds['too_far'] or kinds['dof_inactive'])
    untied = (missed & (kinds['no_face'] | kinds['too_far'])) if classified else set(missed)
    already_fixed = missed - untied
    try:
        solver_errors = collect_solver_errors(directory / (job_name + '.log'))
    except OSError as exc:
        solver_errors = {'file': job_name + '.log', 'count': 0, 'samples': [], 'read_error': str(exc)}
        errors.append({'file': job_name + '.log', 'error': str(exc)})
    coverage = []
    if mesh is not None and missed:
        sides = {}
        for name, elements in mesh['elsets'].items():
            match = re.fullmatch(r'C(\d+)_SA_([0-3])',name)
            if not match:
                continue
            index, face = int(match[1]),int(match[2])
            target = sides.setdefault(index,set())
            for element in elements:
                conn = mesh['elems'][element]
                indices = _TET10_FACES[face] if len(conn)==10 else _TET10_FACES[face][:3]
                target.update(conn[i] for i in indices)
        for index, nodes in sorted(sides.items()):
            item = {'connection':index,'slave_surface_nodes':len(nodes),
                    'reported_missed_nodes':len(nodes & untied),
                    'dof_inactive_on_surface':len(nodes & already_fixed)}
            if connections and index<=len(connections):
                conn = connections[index-1]
                item.update(name=getattr(conn,'name',str(index)),type=type(conn).__name__)
            coverage.append(item)
    return {'requires_review':bool(untied or errors or solver_errors['count']),
            'has_solver_errors':bool(solver_errors['count']), 'solver_errors':solver_errors,
            'reported_missed_nodes':len(missed),
            'untied_nodes':len(untied), 'dof_inactive_nodes':len(already_fixed),
            'untied_node_numbers':sorted(untied)[:500],
            'dof_inactive_node_numbers':sorted(already_fixed)[:500],
            'warning_kinds':{key:len(value & missed) for key, value in kinds.items()} if classified
                            else {'unclassified':len(missed)},
            'reported_node_sample':sorted(untied)[:30],'warning_files':files,'read_errors':errors,
            'connections':coverage,
            'interpretation':'求解器报告部分从面节点未绑定；须检查主从面、位置与覆盖。节点可能参与多条绑定，这不是整体悬浮节点计数；没有警告文件也不代表结构验收通过。'}
