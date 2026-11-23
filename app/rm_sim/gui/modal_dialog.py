"""真实求解振型的阶次切换与归一化动画（模块56 仪表升级）。

升级点（向后兼容 verify_modal_view 既有契约：combo/amplitude/play/timer/
phase/render/toggle/closeEvent 全保留）：
- 频率列表逐阶标注频率值；free_free 结果前 6 阶标注『刚体模态』并明示含义；
- 点面 → 复用 ux 模块 FaceCardPanel 显示该面节点组 |振型| 最大值
  （GL 后端经 cell_data["face_tag"] 与几何/结果视图同链路拾取）；
- 全部文案保留『归一化振型不代表实际振幅』诚实声明。

支持两类结果：单零件旧链路（run_modal，modes.npz）与整装后端 collect
（模块55/56，modes_display.npz + payload，含逐三角形 occ 面 tag）。
"""
import math
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QComboBox,QLabel,QDoubleSpinBox,QPushButton

from .panels import FaceCardPanel

RIGID_FREQ_TOL_HZ = 1e-2  # 刚体模态判定口径（free_free 求解噪声实测 ≤5.7e-3 Hz）


def _unpack(result):
    """统一两类结果 → (npz数据, modes列表, free_free, rigid_modes, 显示幅度, scope)。"""
    if result.get("display_npz"):
        data = np.load(result["display_npz"])
        modes_list = [{"mode": i + 1, "frequency_hz": float(f)}
                      for i, f in enumerate(result["frequencies_hz"])]
        points = data["points"]
        amplitude = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0))) * .05
        free_free = bool(result.get("free_free"))
        rigid = int(result.get("rigid_modes") or 0)
        scope = "整装粘接模态（*TIE）；归一化振型不代表实际振幅。"
        if free_free:
            scope += " free_free：前 6 阶为刚体模态（整体平动/转动，频率≈0，非弹性变形）。"
        return data, modes_list, free_free, rigid, amplitude, scope
    data = np.load(result["npz"])  # 单零件旧链路（verify_modal_analysis/view 既有产物）
    return (data, list(result["modes"]), False, 0,
            float(result["display_amplitude_mm"]),
            "单零件线弹性模态；归一化振型不代表实际振幅。")


class ModalResultDialog(QDialog):
    def __init__(self,result,viewport,parent=None):
        super().__init__(parent)
        self.setWindowTitle("模态频率与归一化振型")
        self.result,self.viewport=result,viewport
        self.data,self.modes_view,self.free_free,self.rigid_modes,self._amplitude_mm,self.scope_text=_unpack(result)
        self.tri_face_tags = (np.asarray(self.data["tri_face_tags"], dtype=np.int64)
                              if "tri_face_tags" in self.data else None)
        self.phase=0.0
        self.last_annotation=None
        self._last_face_rows=None
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel("振型为归一化特征向量：归一化振型不代表实际振幅；"
                                "显示幅度只用于观察形状，动画为慢放示意。"))
        if self.free_free:
            layout.addWidget(QLabel(
                f"free_free 自由悬浮：前 {self.rigid_modes} 阶为刚体模态（频率≈0 Hz）——"
                "含义：结构无约束时的整体平动/转动，不含弹性变形，非结构响应频率。"))
        self.combo=QComboBox()
        for index,entry in enumerate(self.modes_view):
            rigid = " · 刚体模态" if self._is_rigid(index) else ""
            self.combo.addItem(f"第{entry['mode']}阶 · {entry['frequency_hz']:.6g} Hz{rigid}")
        layout.addWidget(self.combo)
        self.amplitude=QDoubleSpinBox();self.amplitude.setRange(.001,1e5);self.amplitude.setSuffix(" mm 显示幅度")
        self.amplitude.setValue(self._amplitude_mm);layout.addWidget(self.amplitude)
        self.play=QPushButton("播放归一化振型");layout.addWidget(self.play)
        self.face_card=FaceCardPanel();layout.addWidget(self.face_card)
        self.timer=QTimer(self);self.timer.setInterval(300)
        self.timer.timeout.connect(self.tick)
        self.play.clicked.connect(self.toggle)
        self.combo.currentIndexChanged.connect(self.render)
        self.amplitude.valueChanged.connect(self.render)
        self.viewport.face_picked.connect(self._on_face_picked)
        self.render()

    def _is_rigid(self,index):
        """刚体模态标注：free_free 结果的前 rigid_modes 阶（|f|≤容差口径）。"""
        if not self.free_free or index >= self.rigid_modes:
            return False
        return abs(float(self.modes_view[index]["frequency_hz"])) <= RIGID_FREQ_TOL_HZ

    def render(self,*args):
        index=self.combo.currentIndex()
        shape=self.data["shapes"][index]
        scale=self.amplitude.value()*(math.sin(self.phase) if self.timer.isActive() else 1.)
        entry=self.modes_view[index]
        title=(f"第{index+1}阶 {entry['frequency_hz']:.6g} Hz"
               + (" · 刚体模态" if self._is_rigid(index) else "")
               + " · 归一化振型（非真实振幅）")
        self.last_annotation=self.viewport.show_modal_fields(
            self.data["points"],self.data["tris"],shape,scale,title,
            face_tags=self.tri_face_tags)
        self._last_face_rows=None  # 换帧后旧面节点组失效，待重新点面

    def tick(self):
        self.phase+=math.pi/8
        self.render()

    def toggle(self):
        if self.timer.isActive():self.timer.stop();self.play.setText("播放归一化振型")
        else:self.timer.start();self.play.setText("暂停")
        self.render()

    def _on_face_picked(self,tag):
        """点面 → 该面节点组 |振型| 最大值进选中面卡片（FaceCardPanel 复用）。"""
        tag=int(tag)
        if tag<=0 or self.tri_face_tags is None:
            self._last_face_rows=None
            self.face_card.set_face({"is_modal_view":True,"tag":tag,"modal_amplitude":None,
                                     "modal_order":None,"modal_frequency_hz":None,"face_node_count":None})
            return
        mask=np.where(self.tri_face_tags==tag)[0]
        rows=np.unique(np.asarray(self.data["tris"])[mask].ravel()) if len(mask) else np.empty(0,dtype=np.int64)
        index=self.combo.currentIndex()
        entry=self.modes_view[index]
        if len(rows)==0:
            self._last_face_rows=None
            self.face_card.set_face({"is_modal_view":True,"tag":tag,"modal_amplitude":None,
                                     "modal_order":index+1,"modal_frequency_hz":entry["frequency_hz"],
                                     "face_node_count":0})
            return
        shape=self.data["shapes"][index]
        amplitude=float(np.linalg.norm(shape[rows],axis=1).max())
        self._last_face_rows=rows
        self.face_card.set_face({"is_modal_view":True,"tag":tag,"modal_amplitude":amplitude,
                                 "modal_order":index+1,"modal_frequency_hz":entry["frequency_hz"],
                                 "face_node_count":int(len(rows))})

    def closeEvent(self,event):
        self.timer.stop()
        try:
            self.viewport.face_picked.disconnect(self._on_face_picked)
        except (RuntimeError,TypeError):
            pass
        self.data.close();super().closeEvent(event)
