"""Transactional advanced-study workbench with typed forms; preparation never runs a solver."""
from copy import deepcopy
from dataclasses import fields,is_dataclass
import types,typing
from pathlib import Path
from PySide6.QtCore import QTimer,QEvent,QCoreApplication,Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QComboBox,QLineEdit,
    QDoubleSpinBox,QSpinBox,QCheckBox,QPushButton,QListWidget,QTableWidget,QTableWidgetItem,
    QDialogButtonBox,QScrollArea,QWidget,QMessageBox,QFileDialog,QInputDialog,
    QAbstractItemView,QTreeWidget,QTreeWidgetItem)
from .theme import chinese_button_box
from ..core.advanced import *


class _WorkbenchTree(QTreeWidget):
    """高级工作台研究树：按研究类型分组的只读分组树（模块43）。"""

    def __init__(self, parent=None):
        QTreeWidget.__init__(self, parent)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

LABEL={'modes':'模态阶数','boundary':'边界模式','boundary_case_index':'模态边界工况','prestress_case':'预应力源case',
 'modal_study_id':'模态来源','start_hz':'起始频率 [Hz]','stop_hz':'终止频率 [Hz]','points':'频率点数',
 'spacing':'频率采样','damping_ratio':'模态阻尼比','load_case_index':'激励工况','phase_deg':'相位 [°]',
 'duration_s':'持续时间 [s]','initial_step_s':'初始步长 [s]','min_step_s':'最小步长 [s]','max_step_s':'最大步长 [s]',
 'output_step_s':'输出间隔 [s]','time':'时间控制','amplitude':'无量纲激励幅值历程','initial_velocity_mm_s':'初速度 [mm/s]',
 'method':'积分方法','load_transfer':'运动载荷传递','mechanism_study_id':'机构来源','source_body_id':'载荷源刚体',
 'target_part_id':'结构目标部件','rotation':'旋转矩阵（9项，逐行）','translation_mm':'坐标平移 [mm]',
 'source_point_mm':'源作用点 [mm]','target_point_mm':'目标作用点 [mm]','gravity_mm_s2':'重力 [mm/s²]',
 'contact_method':'接触方法','allow_free_bodies':'允许未接地刚体自由运动','bodies':'刚体','joints':'关节与驱动',
 'springs':'弹簧阻尼','contacts':'接触对','histories':'驱动时间历程','id':'标识','part_id':'工程部件','fixed':'固定到地面',
 'mass_source':'质量惯量来源','mass_kg':'质量 [kg]（CAD来源时忽略）','inertia_kg_mm2':'惯量张量xx,yy,zz,xy,xz,yz [kg·mm²]',
 'frame':'刚体位姿','origin_mm':'原点 [mm]','quaternion_wxyz':'姿态单位四元数 w,x,y,z',
 'kind':'关节类型','body_a':'对象A','body_b':'对象B','frame_world':'世界关节框架（Z为轴）','limits':'限位下/上限 [rad或mm]',
 'drive':'驱动方式','history_id':'驱动历程标识','unit':'数值单位','samples':'时间[s] / 数值','outside':'采样范围外处理',
 'anchor_a_mm':'A局部锚点 [mm]','anchor_b_mm':'B局部锚点 [mm]','rest_length_mm':'自由长度 [mm]',
 'stiffness_n_mm':'刚度 [N/mm]','damping_n_s_mm':'阻尼 [N·s/mm]','friction':'摩擦系数','restitution':'恢复系数',
 'geometry':'碰撞几何','ground_frame':'地面框架（局部Y=0）','procedure':'分析过程','large_deformation':'几何大变形',
 'plasticity_enabled':'启用塑性','plastic_curves':'真应力—塑性应变曲线','damage':'损伤参数','enabled':'启用',
 'provenance':'材料数据来源','failure_plastic_strain':'失效塑性应变','fracture_energy_n_mm':'断裂能 [N/mm]',
 'calibration_source':'损伤标定来源','model':'模型'}
ENUM={('ModalSettings','boundary'):[('工程固定面','project_fixed'),('自由—自由','free_free')],
 ('HarmonicSettings','spacing'):[('线性','linear'),('对数','log')],
 ('TransientSettings','method'):[('隐式','implicit'),('显式','explicit')],
 ('MechanismSettings','contact_method'):[('NSC','NSC'),('SMC','SMC')],
 ('RigidBodySpec','mass_source'):[('由CAD与密度计算（待解析）','cad_density'),('显式输入','explicit')],
 ('JointSpec','kind'):[('转动','revolute'),('移动','prismatic'),('固定','fixed'),('球铰','spherical')],
 ('JointSpec','drive'):[('无','none'),('位置','position'),('速度','velocity'),('力/矩','effort')],
 ('TimeHistory','unit'):[(x,x) for x in ('factor','N','Nmm','mm','rad','mm/s','rad/s','mm/s2')],
 ('TimeHistory','outside'):[('保持端值','hold'),('置零','zero'),('超范围报错','error')],
 ('ContactPairSpec','geometry'):[('CAD凸包（会简化凹形）','cad_convex_hull'),('CAD网格','cad_mesh')],
 ('NonlinearSettings','procedure'):[('非线性静力','static'),('隐式动力','implicit_dynamic'),('显式动力','explicit_dynamic')],
 ('DamageSettings','model'):[('标量延性损伤（后端待实现）','ductile_scalar')]}


class NumericRows(QWidget):
    def __init__(self,values,headers):
        columns=len(headers)
        super().__init__();layout=QVBoxLayout(self);self.table=QTableWidget(len(values),columns)
        self.table.setHorizontalHeaderLabels(headers)
        for i,row in enumerate(values):
            for j,value in enumerate(row):self.table.setItem(i,j,QTableWidgetItem(str(value)))
        layout.addWidget(self.table);bar=QHBoxLayout()
        add=QPushButton('添加行');add.clicked.connect(lambda:self.table.insertRow(self.table.rowCount()));bar.addWidget(add)
        remove=QPushButton('移除所选行');remove.clicked.connect(lambda:self.table.removeRow(self.table.currentRow()) if self.table.currentRow()>=0 else None);bar.addWidget(remove)
        layout.addLayout(bar)
    def value(self):
        return [tuple(float(self.table.item(i,j).text()) for j in range(self.table.columnCount())) for i in range(self.table.rowCount())]


class RecordList(QWidget):
    def __init__(self,owner,name,item_type,project,context):
        super().__init__();self.owner,self.name,self.item_type=owner,name,item_type
        self.project,self.context=project,context;self.values=deepcopy(getattr(owner,name))
        layout=QVBoxLayout(self);self.list=QListWidget();layout.addWidget(self.list);bar=QHBoxLayout()
        for text,slot in (('添加',self.add),('编辑所选',self.edit),('移除所选',self.remove)):
            button=QPushButton(text);button.clicked.connect(slot);bar.addWidget(button)
        layout.addLayout(bar);self.refresh()
    def refresh(self):
        self.list.clear()
        for i,x in enumerate(self.values):self.list.addItem(f"{i+1}: {getattr(x,'id','')} {getattr(x,'kind','')} {getattr(x,'part_id','')}")
        setattr(self.owner,self.name,deepcopy(self.values))
    def add(self):
        item=self.item_type()
        if hasattr(item,'id'):item.id=self.name.rstrip('s')+str(len(self.values)+1)
        dialog=ObjectDialog(item,self.project,self.context,self)
        if dialog.exec()==dialog.DialogCode.Accepted:self.values.append(dialog.value);self.refresh()
        dialog.deleteLater()
    def edit(self):
        i=self.list.currentRow()
        if i<0:return
        dialog=ObjectDialog(self.values[i],self.project,self.context,self)
        if dialog.exec()==dialog.DialogCode.Accepted:self.values[i]=dialog.value;self.refresh()
        dialog.deleteLater()
    def remove(self):
        i=self.list.currentRow()
        if i>=0:self.values.pop(i);self.refresh()
    def value(self):return deepcopy(self.values)


class ObjectForm(QWidget):
    def __init__(self,obj,project,context):
        super().__init__();self.obj=deepcopy(obj);self.readers={};self.widgets={};form=QFormLayout(self)
        hints=typing.get_type_hints(type(obj))
        for f in fields(obj):
            name=f.name;value=getattr(self.obj,name);hint=hints[name]
            args=typing.get_args(hint);optional=type(None) in args
            if optional:hint=next(t for t in args if t is not type(None))
            options=ENUM.get((type(obj).__name__,name))
            if name in ('boundary_case_index','load_case_index'):
                # 模块43：工况引用改命名下拉（文本=工况名，currentData=索引）——消灭裸整数
                cases = list(project.study.load_cases)
                options = [(lc.name or f'工况{i+1}', i) for i, lc in enumerate(cases)]
                if not options:
                    options = [('（尚未定义工况）', -1)]
            elif name in ('modal_study_id','mechanism_study_id'):
                kind='modal' if name=='modal_study_id' else 'mechanism'
                options=[('请选择','')]+[(s.name,s.id) for s in project.advanced_studies if s.kind==kind]
            elif name in ('part_id','target_part_id'):options=[('请选择部件','')]+[(p.part_id+' '+p.name,p.part_id) for p in project.parts]
            elif name=='source_body_id':
                options=[('请选择','')]+[(s.name+' / '+b.id,b.id) for s in project.advanced_studies if s.kind=='mechanism' for b in s.settings.bodies]
            elif name=='history_id':options=[('无','')]+[(h.id+' ['+h.unit+']',h.id) for h in getattr(context,'histories',[])]
            elif name in ('body_a','body_b'):
                bodies=getattr(context,'bodies',[])
                options=[('地面','ground')]+[(b.id,b.id) for b in bodies]
            if options:
                widget=QComboBox()
                for text,data in options:widget.addItem(text,data)
                index=widget.findData(value)
                if index<0:widget.addItem('未匹配：'+str(value),value);index=widget.count()-1
                widget.setCurrentIndex(index);reader=widget.currentData
            elif isinstance(value,bool):
                widget=QCheckBox();widget.setChecked(value);reader=widget.isChecked
            elif hint is int:
                widget=QSpinBox();widget.setRange(-1000000,1000000);widget.setValue(value);reader=widget.value
            elif hint is float and not optional:
                widget=QDoubleSpinBox();widget.setRange(-1e12,1e12);widget.setDecimals(9);widget.setValue(value);reader=widget.value
            elif is_dataclass(value) or isinstance(hint,type) and hasattr(hint,'__dataclass_fields__'):
                widget=QWidget();box=QVBoxLayout(widget);enable=QCheckBox('启用此项');enable.setChecked(value is not None)
                nested=ObjectForm(value if value is not None else hint(),project,context)
                if optional:box.addWidget(enable);nested.setEnabled(value is not None);enable.toggled.connect(nested.setEnabled)
                box.addWidget(nested);reader=lambda n=nested,e=enable,o=optional:None if o and not e.isChecked() else n.value()
            elif typing.get_origin(hint) is list:
                item=typing.get_args(hint)[0]
                if isinstance(item,type) and hasattr(item,'__dataclass_fields__'):
                    widget=RecordList(self.obj,name,item,project,self.obj if isinstance(self.obj,MechanismSettings) else context)
                else:
                    headers=['等效塑性应变','真应力 [MPa]'] if isinstance(obj,PlasticCurve) else ['时间 [s]','数值 ['+getattr(obj,'unit','')+']']
                    widget=NumericRows(value,headers)
                    if isinstance(obj,TimeHistory) and 'unit' in self.widgets:
                        unit_widget=self.widgets['unit']
                        unit_widget.currentIndexChanged.connect(lambda _i,t=widget.table,u=unit_widget:t.setHorizontalHeaderLabels(['时间 [s]','数值 ['+u.currentData()+']']))
                reader=widget.value
            else:
                widget=QLineEdit('' if value is None else ', '.join(map(str,value)) if isinstance(value,(list,tuple)) else str(value))
                def read(w=widget,h=hint,o=optional):
                    text=w.text().strip()
                    if o and not text:return None
                    if typing.get_origin(h) is tuple:return tuple(float(v.strip()) for v in text.replace('，',',').split(','))
                    if h is float:return float(text)
                    return text
                reader=read
            label='真应力—塑性应变数据' if isinstance(obj,PlasticCurve) and name=='points' else LABEL.get(name,name)
            form.addRow(label,widget);self.readers[name]=reader;self.widgets[name]=widget
    def value(self):
        result=deepcopy(self.obj)
        for name,read in self.readers.items():
            try:setattr(result,name,read())
            except (ValueError,AttributeError) as exc:raise ValueError(LABEL.get(name,name)+'：数据格式不正确') from exc
        return result


class ObjectDialog(QDialog):
    def __init__(self,obj,project,context,parent=None):
        super().__init__(parent);self.setWindowTitle('编辑研究参数');self.resize(850,700);layout=QVBoxLayout(self)
        scroll=QScrollArea();scroll.setWidgetResizable(True);self.form=ObjectForm(obj,project,context);scroll.setWidget(self.form);layout.addWidget(scroll)
        buttons=chinese_button_box(QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel))
        buttons.accepted.connect(self.accept_value);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.value=deepcopy(obj)
    def accept_value(self):
        try:self.value=self.form.value()
        except ValueError as exc:QMessageBox.warning(self,'参数格式',str(exc));return
        self.accept()


class AdvancedWorkbench(QDialog):
    """高级分析工作台（模块43 可理解化）：

    - 研究按类型分组呈现（5 类各一组，组内只列该类研究）；
    - 参数表单按设置数据类生成（每种类型只暴露该类型字段）；
    - 状态列接 validate_studies 实际结论：具体缺失项文本 / 校验通过（绿色），
      不再硬编码『已配置，未计算』；
    - 工况引用为命名下拉（文本=工况名，currentData=索引）；
    - 诚实声明保留：当前高级后端尚未接通——求解按钮禁用且文案含『尚未接通』。
    兼容面保留：project/changed/task/kind 属性、refresh/selected/add/edit/
    toggle/rename/remove/check/export/poll/cancel_preparation/apply/done。
    """

    _ROLE_ID = 1000  # Qt.ItemDataRole.UserRole：条目 → study.id

    def __init__(self,project,parent=None):
        super().__init__(parent);self.project=deepcopy(project);self.changed=False;self.task=None
        self._issues=[];self._global_issues=[]
        self.setWindowTitle('高级分析工作台');self.resize(1050,650);layout=QVBoxLayout(self)
        label=QLabel('配置、校验与导出计算准备包。当前高级后端尚未接通；这里不会启动求解。关闭窗口会取消正在进行的导出，已写文件保留。')
        label.setWordWrap(True);layout.addWidget(label)
        self._green=QColor('#1b5e20');self._red=QColor('#b71c1c');self._muted=QColor('#78909C')
        self.tree=_WorkbenchTree()
        self.tree.setHeaderLabels(['研究（按类型分组）','状态（validate_studies 实际结论）'])
        self.tree.setColumnWidth(0,320);layout.addWidget(self.tree)
        bar=QHBoxLayout();self.kind=QComboBox()
        for key,label in LABELS.items():self.kind.addItem(label,key)
        bar.addWidget(self.kind)
        for text,slot in (('新建研究',self.add),('编辑参数',self.edit),('重命名',self.rename),('启用/停用',self.toggle),('移除所选研究',self.remove)):
            button=QPushButton(text);button.clicked.connect(slot);bar.addWidget(button)
        layout.addLayout(bar);self.status=QLabel('尚未检查配置');self.status.setWordWrap(True);layout.addWidget(self.status)
        bar=QHBoxLayout()
        for text,slot in (('校验配置',self.check),('导出计算准备包',self.export),('取消准备',self.cancel_preparation),('应用研究到工程',self.apply)):
            button=QPushButton(text);button.clicked.connect(slot);bar.addWidget(button)
        close=QPushButton('关闭');close.clicked.connect(self.reject);bar.addWidget(close);layout.addLayout(bar)
        self.solve_button=QPushButton('求解（当前高级后端尚未接通；这里不会启动求解）')
        self.solve_button.setEnabled(False)  # 引擎未接通（含已注册但 engine_connected=False 的适配器）：禁用且文案含『尚未接通』
        layout.addWidget(self.solve_button)
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.poll);self.refresh()
    def _backend_ready(self):
        """引擎是否可求解：适配器已注册且 engine_connected 能力位为真（模块54）。

        modal 适配器已注册可用 prepare（输入编译），但 run/collect 尚未接引擎
        （engine_connected=False）——按钮仍禁用、文案仍『尚未接通』，与未注册
        时不可区分（诚实声明不变，verify_workbench_ux ⑤ 原语义保持）。
        """
        s=self.selected()
        kind=s.kind if s is not None else 'modal'
        try:
            from ..solver.advanced_plan import backend_for
            backend=backend_for(kind)
        except Exception:
            return False
        return bool(getattr(backend,'engine_connected',False))
    def _study_issues(self,s):
        """该研究的 validate_studies 具体缺失项（issue.path 前缀=study.id）。"""
        return [i['message'] for i in self._issues
                if str(i.get('path',''))==s.id or str(i.get('path','')).startswith(s.id+'.')]
    def refresh(self):
        from ..solver.advanced_validation import validate_studies
        self._issues=validate_studies(self.project)
        known={s.id for s in self.project.advanced_studies}
        self._global_issues=[i for i in self._issues if str(i.get('path','')).split('.')[0] not in known]
        self.tree.clear()
        for kind,label in LABELS.items():
            studies=[s for s in self.project.advanced_studies if s.kind==kind]
            group=QTreeWidgetItem([f'{label}',f'{len(studies)} 项'])
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(group)
            for s in studies:
                if not s.enabled:
                    status,level='已停用','muted'
                else:
                    messages=self._study_issues(s)
                    if messages:
                        status,level='⚠ '+'；'.join(messages[:3]),'error'
                    else:
                        status,level='✓ 校验通过（未计算）','ok'
                child=QTreeWidgetItem([s.name,status])
                child.setData(0,self._ROLE_ID,s.id)
                child.setForeground(1,self._green if level=='ok' else (self._red if level=='error' else self._muted))
                child.setToolTip(1,status)
                group.addChild(child)
            group.setExpanded(True)
        global_msgs=[i['message'] for i in self._global_issues]
        if global_msgs:
            self.status.setText('；'.join(global_msgs[:3]))
        self._update_solve_button()
    def _update_solve_button(self):
        """三态如实区分（模块55）：后端未注册→『尚未接通』；已注册且引擎已接通
        （modal）但工作台求解编排（提交/进度/取消 UI）尚未接线→按钮仍禁用、
        文案如实标注；编排接线后才启用。"""
        ready=self._backend_ready()
        orchestration_ready=self._solve_orchestration_ready()
        if not ready:
            self.solve_button.setEnabled(False)
            self.solve_button.setText('求解（当前高级后端尚未接通；这里不会启动求解）')
        elif not orchestration_ready:
            self.solve_button.setEnabled(False)
            self.solve_button.setText('求解（后端已接通；工作台求解编排尚未接通，这里不会启动求解）')
        else:
            self.solve_button.setEnabled(True)
            self.solve_button.setText('求解')
    def _solve_orchestration_ready(self):
        """工作台求解编排（后台提交/进度/取消）尚未接线——模块55 只接通了
        适配器层的 run/collect；此处 False 保持按钮不虚报可点求解。"""
        return False
    def selected(self):
        item=self.tree.currentItem()
        if item is None:return None
        sid=item.data(0,self._ROLE_ID)
        if not sid:return None
        return next((s for s in self.project.advanced_studies if s.id==sid),None)
    def _select_study(self,sid):
        for i in range(self.tree.topLevelItemCount()):
            group=self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                child=group.child(j)
                if child.data(0,self._ROLE_ID)==sid:
                    self.tree.setCurrentItem(child);return
    def add(self):
        study=new_study(self.kind.currentData());self.project.advanced_studies.append(study)
        self.refresh();self._select_study(study.id)
    def edit(self):
        s=self.selected()
        if s is None:return
        dialog=ObjectDialog(s.settings,self.project,s.settings,self)
        if dialog.exec()==dialog.DialogCode.Accepted:s.settings=dialog.value;self.refresh()
        dialog.deleteLater()
    def toggle(self):
        s=self.selected()
        if s:s.enabled=not s.enabled;self.refresh()
    def rename(self):
        s=self.selected()
        if s is None:return
        value,ok=QInputDialog.getText(self,'研究名称','名称',text=s.name)
        if ok and value.strip():s.name=value.strip();self.refresh()
    def remove(self):
        s=self.selected()
        if s is not None:self.project.advanced_studies.remove(s);self.refresh()
    def check(self):
        from ..solver.advanced_validation import validate_studies
        self._issues=validate_studies(self.project)
        self.refresh()
        self.status.setText('\n'.join(x['message'] for x in self._issues) if self._issues else '配置格式与依赖校验通过；几何与求解结果未验证。')
        return self._issues
    def export(self):
        if self.task and self.task.state in ('pending','preparing'):return
        if self.check():return
        parent=QFileDialog.getExistingDirectory(self,'选择准备包父目录',self.project.project_dir)
        if not parent:return
        from ..solver.advanced_tasks import submit_preparation
        self.task=submit_preparation(self.project,parent);self.status.setText('正在后台导出准备包…');self.timer.start()
    def poll(self):
        if not self.task or self.task.state in ('pending','preparing'):return
        self.timer.stop()
        self.status.setText('准备包已导出，未计算：'+str(self.task.directory) if self.task.state=='prepared_not_computed' else self.task.error)
    def cancel_preparation(self):
        if self.task:self.task.cancel_event.set()
    def apply(self):self.changed=True;self.accept()
    def done(self,result):
        if self.task and self.task.state in ('pending','preparing'):self.task.cancel_event.set()
        super().done(result)
