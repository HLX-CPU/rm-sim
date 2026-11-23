# -*- coding: utf-8 -*-
"""面向用户的部件材料、选面、边界载荷及连接配置。

模块38（点面直连工况表单）：
- ``SetupFormWidget``：全部表单控件与逻辑（QWidget，可嵌入停靠面板）——
  主窗口「工况设置」以**非模态 QDockWidget** 挂载本表单（对标属性管理器），
  打开期间三维视口可持续点选：``face_picked`` 实时回填（第一次点→A 面、
  第二次点→B 面，之后点击持续更新 B 面），下拉变更由主窗口反向高亮 3D；
- ``SetupDialog``：QDialog 薄包装（构造器签名 ``(project, model,
  selected_tag=None, parent=None)`` 与 face_a/face_b/kind/mu/material/value/
  traction_distribution、exec()/changed 语义全部保留——兼容红线 B3：
  verify_gui_assembly / verify_consistent_traction / verify_geometry_inspection_gui
  三门按原语义复跑）；
- **消灭静默默认**（交互评审实测 face_b 静默落在 ('p1',1)）：A/B 面下拉
  首项为占位（("", -1)），未选择时 add_definition 拒绝并弹中文提示；
  连接类另要求 B 面已选。
"""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..core.project import Constraint, TractionLoad, PressureLoad, MomentLoad, GravityLoad
from ..core.face_identity import fingerprint_from_face
from ..core.connections import (ConnectionSide, BondedConnection, ContactFrictionConnection,
                               BoltConnection, PinConnection)

_CONNECTION_KINDS = ("粘接", "可分离接触与摩擦", "简化螺栓", "简化销轴")
_FACE_PLACEHOLDER = ("", -1)  # 未选择占位（元组保持 itemData=(part_id,tag) 形状，扫描安全）


class SetupFormWidget(QWidget):
    """工况设置表单（可停靠）：控件与逻辑本体；SetupDialog 包装为对话框。"""

    def __init__(self, project, model, selected_tag=None, parent=None):
        super().__init__(parent)
        self.project, self.model = project, model
        self.changed = False
        self._owners = []
        self._next_pick_target = "face_b" if selected_tag else "face_a"
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "点三维视口中的面即可写入 A/B 面（第一次点→A，第二次点→B），下拉仅作备选。"
            "连接需要两个不同部件的面。"))
        form = QFormLayout()
        self.face_a, self.face_b = QComboBox(), QComboBox()
        for part in project.parts:
            for face in model.faces:
                if face.solid_tag != model.solids[part.solid_index].tag:
                    continue
                center = ", ".join(f"{v:.3g}" for v in face.center)
                normal = ", ".join(f"{v:.2g}" for v in face.normal)
                label = f"{part.name or part.part_id} | 面心 ({center}) mm | 面积 {face.area_mm2:.4g} mm² | 法向 ({normal})"
                for combo in (self.face_a, self.face_b):
                    combo.addItem(label, (part.part_id, int(face.tag)))
                if face.tag == selected_tag:
                    self.face_a.setCurrentIndex(self.face_a.count()-1)
        # 消灭静默默认：A/B 首项占位（未选择），由视口点选或下拉显式选择
        for combo in (self.face_a, self.face_b):
            combo.insertItem(0, "（未选择——点三维视口选面，或在此下拉选择）", _FACE_PLACEHOLDER)
            if selected_tag is None or combo is self.face_b:
                combo.setCurrentIndex(0)
        form.addRow("作用面 / 连接 A 面", self.face_a)
        self.face_a_status = QLabel(); form.addRow("A 面状态", self.face_a_status)
        form.addRow("连接 B 面", self.face_b)
        self.face_b_status = QLabel(); form.addRow("B 面状态", self.face_b_status)
        self.face_a.currentIndexChanged.connect(self._update_face_status)
        self.face_b.currentIndexChanged.connect(self._update_face_status)
        self.pick_hint = QLabel(); self.pick_hint.setWordWrap(True)
        self.pick_hint.setStyleSheet("color: #1565c0;")
        form.addRow("视口点选", self.pick_hint)
        self.name = QLineEdit("工况定义")
        self.kind = QComboBox()
        self.kind.addItems(["固定", "面力", "压力", "力矩", "重力", "粘接", "可分离接触与摩擦", "简化螺栓", "简化销轴"])
        self.value = QDoubleSpinBox()
        self.value.setRange(-1e9, 1e9)
        self.value.setDecimals(5)
        self.value.setValue(1000.0)
        self.direction = QLineEdit("0, -1, 0")
        self.traction_distribution=QComboBox()
        self.traction_distribution.addItem('按面积一致积分（均匀面力）','consistent_area')
        self.traction_distribution.addItem('按节点等分（旧方式）','equal_nodes')
        self.mu = QDoubleSpinBox(); self.mu.setRange(0, 1); self.mu.setSingleStep(.05); self.mu.setValue(.3)
        self.radius = QDoubleSpinBox(); self.radius.setRange(.001, 10000); self.radius.setValue(8)
        self.adjust_nodes = QCheckBox("允许移动从面网格节点贴合主面（可能改变单元质量）")
        self.kind.currentTextChanged.connect(self._kind_changed)
        self.value_label = QLabel("大小")
        form.addRow("名称", self.name)
        form.addRow("类型", self.kind)
        form.addRow(self.value_label, self.value)
        form.addRow("方向 / 力矩轴 (x, y, z)", self.direction)
        form.addRow('面力分配',self.traction_distribution)
        form.addRow("摩擦系数 μ", self.mu)
        form.addRow("螺栓耦合区半径 [mm]", self.radius)
        form.addRow("绑定节点调整", self.adjust_nodes)
        root.addLayout(form)
        self.note = QLabel(); self.note.setWordWrap(True); root.addWidget(self.note)
        self._kind_changed(self.kind.currentText())
        add = QPushButton("添加定义")
        add.clicked.connect(self.add_definition)
        root.addWidget(add)
        from ..core.materials import all_materials
        material_row = QHBoxLayout()
        self.material = QComboBox()
        self.material.addItems([m.name for m in all_materials()])
        material_row.addWidget(QLabel("作用面所属部件材料")); material_row.addWidget(self.material)
        set_material = QPushButton("设置部件材料"); set_material.clicked.connect(self.set_material)
        material_row.addWidget(set_material); root.addLayout(material_row)
        self.definitions = QListWidget(); root.addWidget(self.definitions)
        row = QHBoxLayout()
        remove = QPushButton("移除所选定义"); remove.clicked.connect(self.remove_selected)
        close = QPushButton("完成"); close.clicked.connect(lambda: self._request_close())
        row.addWidget(remove); row.addStretch(); row.addWidget(close); root.addLayout(row)
        self.refresh()
        self._update_face_status()

    # ---- 点面直连（模块38）：视口 face_picked 实时回填 ----
    def on_viewport_face_picked(self, tag: int) -> None:
        """三维视口点击命中面 → 写入当前目标下拉（A→B，之后持续更新 B）。"""
        tag = int(tag)
        if tag <= 0:
            return
        try:
            face = self.model.face_by_tag(tag)
        except KeyError:
            return
        pid = next(
            (
                p.part_id
                for p in self.project.parts
                if 0 <= p.solid_index < len(self.model.solids)
                and self.model.solids[p.solid_index].tag == face.solid_tag
            ),
            None,
        )
        if pid is None:
            self.pick_hint.setText(f"面 tag={tag} 不属于任何部件，未写入表单")
            return
        target = self._next_pick_target
        combo = self.face_a if target == "face_a" else self.face_b
        if not self._set_face_selection(combo, pid, tag):
            self.pick_hint.setText(
                f"面 tag={tag} 不在{'A' if target == 'face_a' else 'B'}面候选中，未写入"
            )
            return
        side_label = "A" if target == "face_a" else "B"
        if target == "face_a":
            self._next_pick_target = "face_b"
            self.pick_hint.setText(
                f"已写入 A 面（面{tag}）；下一次视口点击 → B 面"
            )
        else:
            self.pick_hint.setText(
                f"已写入 B 面（面{tag}）；继续点击将更新 B 面（A 面可用下拉修改）"
            )
        self._update_face_status()

    def _set_face_selection(self, combo, part_id: str, tag: int) -> bool:  # noqa: ANN001
        """按 (part_id, tag) 定位下拉项；未命中返回 False（不静默改选）。"""
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and data[0] == part_id and int(data[1]) == int(tag):
                combo.setCurrentIndex(i)
                return True
        return False

    def _part_name_for(self, part_id: str) -> str:
        part = next((p for p in self.project.parts if p.part_id == part_id), None)
        return (part.name or part.part_id) if part is not None else (part_id or "（未知部件）")

    def _face_status_text(self, combo, required: bool) -> str:  # noqa: ANN001
        data = combo.currentData()
        if data and int(data[1]) > 0:
            pid, tag = data
            try:
                face = self.model.face_by_tag(int(tag))
                return f"已选：{self._part_name_for(pid)} 面{tag}（面积 {face.area_mm2:.4g} mm²）"
            except KeyError:
                return f"已选面 tag={tag} 在当前模型中不存在（几何已更换？）"
        if required:
            return "未选择——点三维视口选面，或在下拉中选择"
        return "未选择"

    def _update_face_status(self, *args) -> None:
        needs_b = self.kind.currentText() in _CONNECTION_KINDS
        self.face_a_status.setText(self._face_status_text(self.face_a, required=True))
        self.face_b_status.setText(self._face_status_text(self.face_b, required=needs_b))
        if not self.pick_hint.text():
            self.pick_hint.setText(
                f"下一次视口点击将写入：{'A 面' if self._next_pick_target == 'face_a' else 'B 面'}"
            )

    def _request_close(self) -> None:
        """『完成』按钮：向上找宿主——停靠面板则 close（走主窗口 eventFilter 收尾），
        对话框则 accept（exec 语义不变）。"""
        from PySide6.QtWidgets import QDockWidget

        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QDockWidget):
                widget.close()
                return
            if isinstance(widget, QDialog):
                widget.accept()
                return
            widget = widget.parentWidget()

    def _kind_changed(self, name):
        units = {"面力": "总力 [N]", "压力": "压力 [MPa]", "力矩": "力矩 [N·mm]",
                 "重力": "加速度 [mm/s²]", "简化螺栓": "等效预紧力 [N]"}
        self.value_label.setText(units.get(name, "大小（此类型不使用）"))
        self.value.setEnabled(name in units)
        self.direction.setEnabled(name in ("面力", "力矩", "重力"))
        self.traction_distribution.setEnabled(name=='面力')
        self.face_b.setEnabled(name in _CONNECTION_KINDS)
        self.mu.setEnabled(name == "可分离接触与摩擦")
        self.radius.setEnabled(name == "简化螺栓")
        self.adjust_nodes.setEnabled(name in ("粘接", "简化销轴"))
        if name == "重力":
            self.value.setValue(9810)
        notes = {"粘接": "两面完全绑定，不能滑移或分离；默认保留网格节点位置。",
                 "可分离接触与摩擦": "法向仅传压，切向库仑摩擦；打开或失稳时可能无法得到静力平衡。",
                 "简化螺栓": "耦合面刚化并施加一步等效预紧；不模拟真实螺纹、杆件拉伸及预紧锁定步骤。",
                 "简化销轴": "销体与孔壁面零间隙绑定；不模拟间隙、孔壁挤压接触及相对转动。",
                 "重力": "重力作用于所有部件，使用各自材料的密度。"}
        self.note.setText(notes.get(name, "方向会自动归一化；正压力压向面内。"))
        self._update_face_status()

    def refresh(self):
        case = self.project.study.load_cases[0]
        self._owners = [(case.constraints, x) for x in case.constraints] + [(case.loads, x) for x in case.loads] + [
            (self.project.connections, x) for x in self.project.connections]
        self.definitions.clear()
        for _, item in self._owners:
            self.definitions.addItem(item.summary())

    def add_definition(self):
        try:
            kind, name = self.kind.currentText(), self.name.text().strip() or self.kind.currentText()
            a_data = self.face_a.currentData()
            if not a_data or not a_data[0] or int(a_data[1]) <= 0:
                raise ValueError(
                    "作用面 A 未选择：请点三维视口中的面（第一次点→A），或在下拉中选择"
                )
            pid, tag = a_data
            face = self.model.face_by_tag(tag)
            target = dict(name=name, part_id=pid, face_tag=tag, face=fingerprint_from_face(face))
            value = self.value.value()
            direction = tuple(float(x.strip()) for x in self.direction.text().replace("，", ",").split(","))
            case = self.project.study.load_cases[0]
            if kind == "固定":
                item = Constraint(**target); collection = case.constraints
            elif kind == "面力":
                item = TractionLoad(**target, total_force_n=value, direction=direction,
                    distribution=self.traction_distribution.currentData()); collection = case.loads
            elif kind == "压力":
                item = PressureLoad(**target, pressure_mpa=value); collection = case.loads
            elif kind == "力矩":
                item = MomentLoad(**target, moment_nmm=value, axis=direction); collection = case.loads
            elif kind == "重力":
                item = GravityLoad(name=name, g_mm_s2=value, direction=direction); collection = case.loads
            else:
                b_data = self.face_b.currentData()
                if not b_data or not b_data[0] or int(b_data[1]) <= 0:
                    raise ValueError(
                        "连接 B 面未选择：请点三维视口中的第二面（第一次点→A、第二次点→B），"
                        "或在下拉中选择；不再默认第一部件第一个面"
                    )
                other_pid, other_tag = b_data
                sides = dict(name=name, side_a=ConnectionSide(pid, fingerprint_from_face(face)),
                             side_b=ConnectionSide(other_pid, fingerprint_from_face(self.model.face_by_tag(other_tag))))
                cls = {"粘接": BondedConnection, "可分离接触与摩擦": ContactFrictionConnection,
                       "简化螺栓": BoltConnection, "简化销轴": PinConnection}[kind]
                extra = {"friction_mu": self.mu.value()} if kind == "可分离接触与摩擦" else (
                    {"pretension_n": value, "coupling_radius_mm": self.radius.value()} if kind == "简化螺栓" else {})
                if kind in ("粘接", "简化销轴"):
                    extra['adjust_nodes'] = self.adjust_nodes.isChecked()
                item = cls(**sides, **extra); collection = self.project.connections
            item.validate()
            collection.append(item)
            self.project.dirty = self.changed = True
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "定义未添加", str(exc))

    def set_material(self):
        from ..core.materials import set_part_material
        try:
            pid, _ = self.face_a.currentData()
            set_part_material(self.project, pid, self.material.currentText())
            self.project.dirty = self.changed = True
        except Exception as exc:
            QMessageBox.warning(self, "材料未设置", str(exc))

    def remove_selected(self):
        index = self.definitions.currentRow()
        if 0 <= index < len(self._owners):
            collection, item = self._owners[index]
            collection.remove(item)
            self.project.dirty = self.changed = True
            self.refresh()


class SetupDialog(QDialog):
    """模态对话框薄包装：内嵌 SetupFormWidget；未定义属性转发表单（红线 B3 兼容）。

    候选面编辑流程（_edit_candidate_faces）与三个绿色门（verify_gui_assembly /
    verify_consistent_traction / verify_geometry_inspection_gui 的 exec monkeypatch）
    按原语义继续使用本类。
    """

    def __init__(self, project, model, selected_tag=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("材料、约束、载荷与部件连接")
        self.resize(850, 740)
        self.form = SetupFormWidget(project, model, selected_tag)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.form)

    def __getattr__(self, name):
        if name == "form":
            raise AttributeError(name)
        try:
            form = object.__getattribute__(self, "form")
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(form, name)
