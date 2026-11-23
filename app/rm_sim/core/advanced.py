"""Engine-neutral advanced studies. Units are explicit; configuration is not a result."""
from __future__ import annotations
from dataclasses import dataclass,field
import uuid


@dataclass
class TimeHistory:
    id: str = 'history1'
    unit: str = 'factor'
    samples: list[tuple[float,float]] = field(default_factory=lambda:[(0.,0.),(1.,1.)])
    outside: str = 'hold'  # hold / zero / error; interpolation is piecewise linear


@dataclass
class AnalysisFrame:
    origin_mm: tuple[float,float,float] = (0.,0.,0.)
    quaternion_wxyz: tuple[float,float,float,float] = (1.,0.,0.,0.)


@dataclass
class RigidBodySpec:
    id: str = 'body1'
    part_id: str = ''
    fixed: bool = False
    mass_source: str = 'cad_density'
    mass_kg: float | None = None
    inertia_kg_mm2: tuple[float,float,float,float,float,float] | None = None  # xx yy zz xy xz yz
    frame: AnalysisFrame = field(default_factory=AnalysisFrame)


@dataclass
class JointSpec:
    id: str = 'joint1'
    kind: str = 'revolute'  # revolute / prismatic / fixed / spherical
    body_a: str = 'ground'
    body_b: str = 'body1'
    frame_world: AnalysisFrame = field(default_factory=AnalysisFrame)  # local Z is joint axis
    limits: tuple[float,float] | None = None  # rad for revolute; mm for prismatic
    drive: str = 'none'  # none / position / velocity / effort
    history_id: str = ''


@dataclass
class SpringDamperSpec:
    id: str = 'spring1'
    body_a: str = 'ground'
    body_b: str = 'body1'
    anchor_a_mm: tuple[float,float,float] = (0.,0.,0.)  # body-local, ground in world
    anchor_b_mm: tuple[float,float,float] = (0.,0.,0.)
    rest_length_mm: float = 1.
    stiffness_n_mm: float = 1.
    damping_n_s_mm: float = 0.


@dataclass
class ContactPairSpec:
    body_a: str = 'ground'
    body_b: str = 'body1'
    friction: float = .3
    restitution: float = 0.
    geometry: str = 'cad_convex_hull'
    ground_frame: AnalysisFrame = field(default_factory=AnalysisFrame)  # plane local Y=0


@dataclass
class TimeControls:
    duration_s: float = 1.
    initial_step_s: float = .001
    min_step_s: float = .000001
    max_step_s: float = .01
    output_step_s: float = .01


@dataclass
class ModalSettings:
    modes: int = 12
    boundary: str = 'project_fixed'  # project_fixed / free_free
    prestress_case: str = ''
    boundary_case_index: int = 0


@dataclass
class HarmonicSettings:
    modal_study_id: str = ''
    start_hz: float = 1.
    stop_hz: float = 1000.
    points: int = 200
    spacing: str = 'linear'
    damping_ratio: float = .02
    load_case_index: int = 0
    phase_deg: float = 0.


@dataclass
class LoadTransferSpec:
    mechanism_study_id: str = ''
    source_body_id: str = ''
    target_part_id: str = ''
    rotation: tuple[float,...] = (1.,0.,0.,0.,1.,0.,0.,0.,1.)
    translation_mm: tuple[float,float,float] = (0.,0.,0.)
    source_point_mm: tuple[float,float,float] = (0.,0.,0.)
    target_point_mm: tuple[float,float,float] = (0.,0.,0.)


@dataclass
class TransientSettings:
    time: TimeControls = field(default_factory=TimeControls)
    load_case_index: int = 0
    amplitude: TimeHistory = field(default_factory=TimeHistory)
    initial_velocity_mm_s: tuple[float,float,float] = (0.,0.,0.)
    method: str = 'implicit'
    load_transfer: LoadTransferSpec | None = None


@dataclass
class MechanismSettings:
    time: TimeControls = field(default_factory=TimeControls)
    gravity_mm_s2: tuple[float,float,float] = (0.,-9810.,0.)
    contact_method: str = 'NSC'
    allow_free_bodies: bool = False
    bodies: list[RigidBodySpec] = field(default_factory=list)
    joints: list[JointSpec] = field(default_factory=list)
    springs: list[SpringDamperSpec] = field(default_factory=list)
    contacts: list[ContactPairSpec] = field(default_factory=list)
    histories: list[TimeHistory] = field(default_factory=list)


@dataclass
class PlasticCurve:
    part_id: str = ''
    points: list[tuple[float,float]] = field(default_factory=list)  # equivalent plastic strain, true stress MPa
    provenance: str = ''


@dataclass
class DamageSettings:
    enabled: bool = False
    model: str = 'ductile_scalar'
    failure_plastic_strain: float | None = None
    fracture_energy_n_mm: float | None = None
    calibration_source: str = ''


@dataclass
class NonlinearSettings:
    time: TimeControls = field(default_factory=TimeControls)
    procedure: str = 'static'  # static / implicit_dynamic / explicit_dynamic
    large_deformation: bool = True
    load_case_index: int = 0
    amplitude: TimeHistory = field(default_factory=TimeHistory)
    plasticity_enabled: bool = False
    plastic_curves: list[PlasticCurve] = field(default_factory=list)
    damage: DamageSettings = field(default_factory=DamageSettings)


SETTINGS={'modal':ModalSettings,'harmonic':HarmonicSettings,'transient':TransientSettings,
          'mechanism':MechanismSettings,'nonlinear':NonlinearSettings}
LABELS={'modal':'整装模态','harmonic':'受迫振动 / 扫频','transient':'瞬态 / 冲击',
        'mechanism':'机构运动与载荷历程','nonlinear':'非线性 / 塑性 / 损伤'}


@dataclass
class AdvancedStudy:
    id: str
    name: str
    kind: str
    settings: ModalSettings | HarmonicSettings | TransientSettings | MechanismSettings | NonlinearSettings
    enabled: bool = True
    depends_on: list[str] = field(default_factory=list)


def new_study(kind,name=None):
    if kind not in SETTINGS:raise ValueError('未知高级研究类型：'+kind)
    return AdvancedStudy(uuid.uuid4().hex,name or LABELS[kind],kind,SETTINGS[kind]())


ADVANCED_TYPES=(TimeHistory,AnalysisFrame,RigidBodySpec,JointSpec,SpringDamperSpec,ContactPairSpec,
    TimeControls,ModalSettings,HarmonicSettings,LoadTransferSpec,TransientSettings,MechanismSettings,
    PlasticCurve,DamageSettings,NonlinearSettings,AdvancedStudy)
