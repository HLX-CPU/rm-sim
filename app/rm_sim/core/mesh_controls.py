"""Persistent local refinement identities; runtime CAD tags are never identities."""
from dataclasses import dataclass
import math
from .face_identity import FaceFingerprint


@dataclass(frozen=True)
class EdgeFingerprint:
    length_mm: float
    center: tuple[float,float,float]
    bbox: tuple[float,float,float,float,float,float]
    curve_type: str


@dataclass
class MeshControl:
    name: str = '局部加密'
    kind: str = 'face'  # part / face / edge
    part_id: str = ''
    size_mm: float = .5
    transition_mm: float = 3.
    face: FaceFingerprint | None = None
    edge: EdgeFingerprint | None = None
    enabled: bool = True

    def validate(self, global_size):
        if self.kind not in ('part','face','edge'):
            raise ValueError('未知局部网格目标类型：'+self.kind)
        if not math.isfinite(self.size_mm) or not 0<self.size_mm<=global_size:
            raise ValueError(f'「{self.name}」局部尺寸必须为正且不大于全局尺寸')
        if not math.isfinite(self.transition_mm) or self.transition_mm<=0:
            raise ValueError(f'「{self.name}」过渡距离必须为正')
        if self.kind=='face' and self.face is None:
            raise ValueError(f'「{self.name}」缺少面身份，请重新选择')
        if self.kind=='edge' and self.edge is None:
            raise ValueError(f'「{self.name}」缺少边身份，请重新选择')
