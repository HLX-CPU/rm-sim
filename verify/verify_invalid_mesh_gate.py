"""A genuinely inverted quadratic element must stop preparation before a deck exists."""
from assembly_check_utils import ROOT, evidence_dir


def main():
    import gmsh
    from rm_sim.cad.step_import import import_step
    from rm_sim.core.project import Project, LoadCase, Constraint, TractionLoad
    from rm_sim.solver.assembly_workflow import prepare_analysis
    work = evidence_dir('invalid_mesh_gate')
    model = import_step(ROOT/'samples/beam_100x10x10.step')
    project = Project(project_dir=str(work))
    project.study.load_cases = [LoadCase(name='真实畸变网格拒绝',
        constraints=[Constraint(name='固定', face_tag=1)],
        loads=[TractionLoad(name='力', face_tag=2, total_force_n=1.)])]
    original = gmsh.model.mesh.generate
    def generate_with_inversion(dim):
        original(dim)
        types, _, blocks = gmsh.model.mesh.getElements(3)
        block = next(b for t,b in zip(types,blocks) if t == 11)
        node = int(block[4])  # a real tetra10 midside node, not a quality-stat mock
        xyz, uv, _, _ = gmsh.model.mesh.getNode(node)
        gmsh.model.mesh.setNode(node, [float(xyz[0])+1000, float(xyz[1])+1000, float(xyz[2])+1000], uv)
    gmsh.model.mesh.generate = generate_with_inversion
    try:
        try:
            prepare_analysis(project, model, work/'invalid.inp')
        except ValueError as exc:
            assert '负Jacobian' in str(exc), str(exc)
            (work/'rejection.txt').write_text(str(exc), encoding='utf-8')
        else:
            raise AssertionError('inverted mesh reached deck creation')
        assert (work/'invalid.inp').is_file(), 'retain the rejected mesh for diagnosis'
        assert not (work/'cases').exists(), 'no solve was submitted'
    finally:
        gmsh.model.mesh.generate = original
        model.close()
    print('VERIFY_INVALID_MESH_GATE: PASS', work, flush=True)


if __name__ == '__main__':
    main()
