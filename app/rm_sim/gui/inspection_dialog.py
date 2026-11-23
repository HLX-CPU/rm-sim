"""Geometric inspection results; candidates require explicit modeling decisions."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QTabWidget,QPushButton,QTableWidget
from .result_dialogs import _readonly_table


class AssemblyInspectionDialog(QDialog):
    faces_selected=Signal(int,int)

    def __init__(self,result,parent=None):
        super().__init__(parent)
        self.setWindowTitle('装配间隙、干涉与连接候选')
        self.resize(1080,650)
        layout=QVBoxLayout(self)
        geometry=result['geometry'];faces=result['contact_faces']
        label=QLabel(f"检查{geometry['checked_pairs']}对实体；体积干涉{geometry['interference_count']}对；"
                     f"检查失败{geometry['failed_pairs']+len(faces['errors'])}项。几何检查不代替力学验证。")
        label.setWordWrap(True);layout.addWidget(label)
        tabs=QTabWidget();layout.addWidget(tabs)
        labels={'interference':'体积干涉','zero_distance':'零距离（接触性质待确认）','separated':'分离','check_failed':'检查失败'}
        pairs=sorted(geometry['pairs'],key=lambda r:({'check_failed':0,'interference':1,'zero_distance':2,'separated':3}[r['classification']],r['a'],r['b']))
        rows=[(r['name_a'],r['name_b'],labels[r['classification']],
               '未知' if 'distance_mm' not in r else f"{r['distance_mm']:.8g}",
               '未知' if 'intersection_mm3' not in r else f"{r['intersection_mm3']:.8g}",r.get('error','')) for r in pairs]
        self.geometry_table=_readonly_table(rows,['部件A','部件B','几何关系','距离 [mm]','交叠 [mm³]','失败原因'])
        tabs.addTab(self.geometry_table,'实体检查')
        self.candidates=faces['candidates']
        rows=[(r['name_a'],r['name_b'],str(r['face_a']),str(r['face_b']),f"{r['common_area_mm2']:.8g}",
               r.get('geometry_type','planar')) for r in self.candidates]
        self.contact_table=_readonly_table(rows,['部件A','部件B','面A','面B','公共面积 [mm²]','几何类型'])
        self.contact_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.contact_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        tabs.addTab(self.contact_table,'接触面候选')
        hint=QLabel('公共面积只说明几何抵接。请在编辑器中选择粘接、可分离接触或其他连接；不会自动添加连接。'
                    '本次默认只搜索平面，包括几何上为平面的BSpline编码；曲面和正间隙连接仍需人工检查。')
        hint.setWordWrap(True);layout.addWidget(hint)
        use=QPushButton('将所选两面带入连接编辑器');use.setEnabled(bool(self.candidates));use.clicked.connect(self.use_selected)
        layout.addWidget(use)
        if self.candidates:self.contact_table.selectRow(0)
        close=QPushButton('关闭');close.clicked.connect(self.accept);layout.addWidget(close)

    def use_selected(self):
        row=self.contact_table.currentRow()
        if row>=0:
            candidate=self.candidates[row]
            self.faces_selected.emit(candidate['face_a'],candidate['face_b'])
