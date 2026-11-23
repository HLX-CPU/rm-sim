"""Background preparation only. This scheduler has no solver execution path."""
from dataclasses import dataclass,field
from pathlib import Path
import threading,datetime,uuid
from .advanced_plan import write_preparation_package


class PreparationCancelled(RuntimeError):
    is_cancellation=True


@dataclass
class AdvancedPreparationTask:
    directory: Path
    state: str = 'pending'
    error: str = ''
    solver_executed: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)


def submit_preparation(project,parent):
    from ..core.store import from_dict,to_dict
    snapshot=from_dict(to_dict(project))
    path=Path(parent)/('advanced_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    task=AdvancedPreparationTask(path)
    def check():
        if task.cancel_event.is_set():raise PreparationCancelled('准备已取消，已写文件保留')
    def work():
        task.state='preparing'
        try:
            write_preparation_package(snapshot,path,check_cancel=check)
            task.state='prepared_not_computed'
        except PreparationCancelled as exc:task.error=str(exc);task.state='cancelled'
        except Exception as exc:task.error=str(exc);task.state='failed'
    threading.Thread(target=work,daemon=True,name=path.name).start()
    return task
