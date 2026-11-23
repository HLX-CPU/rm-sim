"""只读核对最新回归、交付工程和启动入口，落盘可追溯验收清单。"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile
from assembly_check_utils import ROOT,evidence_dir


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    tests={};history=[]
    for summary in sorted(Path('E:/rm_sim/verification').glob('regression_20261102_*/summary.json')):
        for row in json.loads(summary.read_text(encoding='utf-8')):
            item={**row,'summary':str(summary)}
            history.append(item);tests[row['check']]=item
    assert len(tests)>=27,len(tests)
    for name,item in tests.items():
        text=Path(item['stdout']).read_text(encoding='utf-8',errors='replace')
        assert item['exit_code']==0,(name,item)
        assert re.search(r'VERIFY_[A-Z_]+:\s*PASS',text),name
        assert not re.search(r'\[FAIL\]',text),name
        item['stdout_sha256']=sha(item['stdout'])
        item['stderr_sha256']=sha(item['stderr'])
    work=evidence_dir('delivery_check')
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1',TEMP='E:/rm_sim/temp',TMP='E:/rm_sim/temp')
    run=subprocess.run(['cmd.exe','/d','/c',str(ROOT/'启动仿真软件.bat'),'--selfcheck'],cwd=ROOT,env=env,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
    (work/'launcher_stdout.log').write_bytes(run.stdout);(work/'launcher_stderr.log').write_bytes(run.stderr)
    assert run.returncode==0 and b'selfcheck PASS' in run.stdout,(run.returncode,run.stdout[-1000:],run.stderr[-1000:])
    examples={}
    for name in ('part_beam','assembly_contact','modal_beam','chrono_demo'):
        result=ROOT/'examples'/name/'batch_result.json'
        data=json.loads(result.read_text(encoding='utf-8'));assert data['batch_ok'],name
        examples[name]={'result':str(result),'sha256':sha(result)}
        if name in ('part_beam','assembly_contact'):
            bundle=ROOT/'examples'/name/'project.rmsimproject'
            with zipfile.ZipFile(bundle) as z:
                meta=json.loads(z.read('project.json'))
                assert meta['schema']=='rm_sim.bundle@1'
                assert z.read(meta['geometry']['bundled'])
                if name=='assembly_contact':
                    assert len(meta['project']['parts'])==2
                    assert len(meta['part_geometries'])==2
                    assert all(z.read(v) for v in meta['part_geometries'].values())
                    assert len(meta['project']['connections'])==1
            examples[name]['bundle']={'path':str(bundle),'bytes':bundle.stat().st_size,'sha256':sha(bundle)}
    files={}
    for p in sorted(list((ROOT/'app').rglob('*.py'))+list((ROOT/'verify').glob('*.py'))):
        ast.parse(p.read_text(encoding='utf-8'));files[str(p.relative_to(ROOT))]=sha(p)
    for p in [ROOT/'requirements.txt',ROOT/'启动仿真软件.bat',ROOT/'使用说明.md',ROOT/'交付报告.md']:
        assert p.stat().st_size>0
        files[str(p.relative_to(ROOT))]=sha(p)
    record={
        'status':'software_regression_passed_with_explicit_physical_scope_limits',
        'unique_checks':len(tests),'latest_checks':tests,
        'earlier_failures':[x for x in history if x['exit_code']!=0],
        'launcher':{'exit_code':run.returncode,'evidence':str(work)},'examples':examples,'source_sha256':files,
        'unmet_original_criterion':[{'criterion':'钢铝串联杆仅排除界面1×MeshSize后的全节点最大Mises≈10MPa±10%',
             'status':'not_met','note':'原节点峰值门不能用旧脚本扩大排除区域后的远场PASS替代。新增假设材料/允许泊松收缩的解析补丁全节点门单独通过。'}],
        'not_verified':['本轮原生GL桌面交互人工验收','真实RoboMaster结构与任意复杂接触工况','制造公差与工程安全结论'],
        'not_implemented':['跌落冲击应力','塑性与损伤','装配或预应力模态','通用机构编辑器','双向或瞬态跨求解器耦合']}
    destination=ROOT/'验证结果_2026-11-02.json'
    destination.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'path':str(destination),'checks':len(tests),'latest_passed':True,
                     'history_failure_count':len(record['earlier_failures']),'launcher_exit':run.returncode},ensure_ascii=False))


if __name__=='__main__':main()
