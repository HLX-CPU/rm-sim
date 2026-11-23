"""Local mesh settings and bounded quality diagnostics; no live screen automation."""
from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QComboBox,
    QDoubleSpinBox,QPushButton,QTableWidget,QTableWidgetItem,QDialogButtonBox,QMessageBox)
from ..core.mesh_controls import MeshControl
from ..core.face_identity import fingerprint_from_face
from .theme import chinese_button_box


class MeshControlsDialog(QDialog):
    def __init__(self,project,model,parent=None):
        super().__init__(parent)
        self.project,self.model=project,model
        self.controls=deepcopy(project.study.mesh_controls)
        self.setWindowTitle('局部网格设置');self.resize(980,600)
        layout=QVBoxLayout(self)
        note=QLabel('局部尺寸为目标值；曲率、几何及质量优化也会影响实际单元大小。多个设置取较小尺寸。')
        note.setWordWrap(True);layout.addWidget(note)
        form=QFormLayout();self.global_size=self.spin(project.study.mesh_size_mm,.2,50)
        form.addRow('全局尺寸 [mm]',self.global_size)
        self.part=QComboBox()
        for p in project.parts:self.part.addItem(f'{p.part_id} {p.name}',p.part_id)
        if not project.parts and len(model.solids)==1:self.part.addItem('当前零件','')
        self.kind=QComboBox()
        for label,value in (('部件','part'),('面','face'),('边','edge')):self.kind.addItem(label,value)
        self.target=QComboBox();self.target.setMinimumContentsLength(35)
        self.size=self.spin(min(.5,project.study.mesh_size_mm),.01,50)
        self.transition=self.spin(3.,.01,1000)
        for label,widget in (('所属部件',self.part),('目标类型',self.kind),('几何目标',self.target),
                             ('局部尺寸 [mm]',self.size),('过渡距离 [mm]（面/边）',self.transition)):
            form.addRow(label,widget)
        layout.addLayout(form)
        self.part.currentIndexChanged.connect(self.refresh_targets)
        self.kind.currentIndexChanged.connect(self.refresh_targets)
        self.table=QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(['名称','部件','类型','目标尺寸 [mm]','启用'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        row=QHBoxLayout()
        for text,slot in (('添加设置',self.add_control),('替换所选设置',self.replace_control),
                          ('启用/停用所选',self.toggle_control),('移除所选设置',self.remove_control)):
            button=QPushButton(text);button.clicked.connect(slot);row.addWidget(button)
        layout.addLayout(row)
        buttons=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel))
        buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.refresh_targets();self.refresh_table()

    @staticmethod
    def spin(value,minimum,maximum):
        spin=QDoubleSpinBox();spin.setRange(minimum,maximum);spin.setDecimals(3);spin.setValue(value);return spin

    def refresh_targets(self,*args):
        from ..cad.mesh_targets import targets_for_part
        self.target.clear()
        try:
            solid,faces,edges=targets_for_part(self.project,self.model,self.part.currentData() or '')
            kind=self.kind.currentData()
            if kind=='part':self.target.addItem(f'整个部件（实体 {solid}）',None)
            elif kind=='face':
                for f in faces:
                    self.target.addItem(f'面 {f.tag} | {f.area_mm2:.5g} mm² | 中心 {tuple(round(v,3) for v in f.center)}',f)
            else:
                for tag,fp in edges:
                    self.target.addItem(f'边 {tag} | {fp.length_mm:.5g} mm | 中心 {tuple(round(v,3) for v in fp.center)}',fp)
        except Exception as exc:
            self.target.addItem('目标读取失败：'+str(exc),None)
            self.target.setCurrentIndex(-1)

    def selected_control(self):
        if self.target.currentIndex()<0:raise ValueError('请先选择有效几何目标')
        kind=self.kind.currentData(); target=self.target.currentData()
        c=MeshControl(name=f'{self.part.currentText()} / {self.target.currentText().split("|")[0].strip()}',
            kind=kind,part_id=self.part.currentData() or '',size_mm=self.size.value(),
            transition_mm=self.transition.value(),face=fingerprint_from_face(target) if kind=='face' else None,
            edge=target if kind=='edge' else None)
        c.validate(self.global_size.value());return c

    def add_control(self):
        try:self.controls.append(self.selected_control());self.refresh_table()
        except ValueError as exc:QMessageBox.warning(self,'局部网格设置',str(exc))

    def replace_control(self):
        i=self.table.currentRow()
        if i<0:return
        try:self.controls[i]=self.selected_control();self.refresh_table()
        except ValueError as exc:QMessageBox.warning(self,'局部网格设置',str(exc))

    def toggle_control(self):
        i=self.table.currentRow()
        if i>=0:self.controls[i].enabled=not self.controls[i].enabled;self.refresh_table()

    def remove_control(self):
        i=self.table.currentRow()
        if i>=0:self.controls.pop(i);self.refresh_table()

    def refresh_table(self):
        self.table.setRowCount(len(self.controls))
        for i,c in enumerate(self.controls):
            for j,value in enumerate((c.name,c.part_id,c.kind,f'{c.size_mm:g}','是' if c.enabled else '否')):
                self.table.setItem(i,j,QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def apply(self):
        try:
            from ..cad.mesh_targets import resolve_controls
            snapshot=deepcopy(self.project)
            snapshot.study.mesh_controls=deepcopy(self.controls)
            resolve_controls(snapshot,self.model,self.global_size.value())
        except ValueError as exc:
            QMessageBox.warning(self,'局部网格设置',str(exc));return
        self.accept()


class MeshQualityDialog(QDialog):
    def __init__(self,quality,parent=None):
        super().__init__(parent);self.setWindowTitle('网格质量与差单元位置');self.resize(950,550)
        layout=QVBoxLayout(self)
        if not quality:
            layout.addWidget(QLabel('此记录没有逐单元质量数据。请生成新网格；旧结果不补写质量结论。'))
        else:
            p=quality['sicn_percentiles'];counts=quality['below_sicn']
            label=QLabel(f"单元 {quality['element_count']}；非正Jacobian {quality['nonpositive_jacobian_count']}；"
                f"SICN 最小 {p['0']:.5g} / P1 {p['1']:.5g} / 中位 {p['50']:.5g}\n"
                f"SICN<0.01: {counts['0.01']}；<0.05: {counts['0.05']}；<0.1: {counts['0.1']}。"
                '这些是质量分布提示，不替代网格收敛检查。')
            label.setWordWrap(True);layout.addWidget(label)
            worst=quality['worst_elements'];table=QTableWidget(len(worst),5)
            table.setHorizontalHeaderLabels(['INP单元号','部件','SICN','minDetJac','定位坐标 [mm]'])
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            for i,e in enumerate(worst):
                for j,v in enumerate((e['element'],e['part'],f"{e['sicn']:.7g}",f"{e['min_det_jac']:.7g}",
                                      ', '.join(f'{x:.5g}' for x in e['center_mm']))):
                    table.setItem(i,j,QTableWidgetItem(str(v)))
            table.resizeColumnsToContents();layout.addWidget(table)
            layout.addWidget(QLabel(f"仅列最差{len(worst)}个单元。{quality['center_note']}。"))
        close=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Close))
        close.rejected.connect(self.reject);layout.addWidget(close)
