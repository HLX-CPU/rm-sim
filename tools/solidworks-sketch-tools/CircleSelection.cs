using System.Collections.Generic;
using System.Runtime.InteropServices;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace SketchQuickTools
{
    public static class CircleSelection
    {
        internal static ISketch GetActiveSketch(ISldWorks app, IModelDoc2 model)
        {
            if (model == null)
                return null;

            var kind = model.GetType();
            if (kind == (int)swDocumentTypes_e.swDocASSEMBLY)
            {
                var assembly = (IAssemblyDoc)model;
                object editTarget = assembly.GetEditTarget();
                if (editTarget != null && app.IsSame(model, editTarget) != (int)swObjectEquality.swObjectSame)
                    return null;
            }
            else if (kind != (int)swDocumentTypes_e.swDocPART)
            {
                return null;
            }

            ISketch sketch = model.SketchManager.ActiveSketch;
            return sketch != null && !sketch.Is3D() ? sketch : null;
        }

        public static string Select(ISldWorks app, IModelDoc2 model)
        {
            ISketch sketch = GetActiveSketch(app, model);
            if (sketch == null)
                return "请先编辑零件或装配体自身的二维草图。";

            var targets = new List<DispatchWrapper>();
            var entities = sketch.GetSketchSegments() as object[];
            if (entities != null)
            {
                foreach (object entity in entities)
                {
                    var segment = entity as ISketchSegment;
                    if (segment == null || segment.GetType() != (int)swSketchSegments_e.swSketchARC ||
                        segment.Status != (int)swConstrainedStatus_e.swUnderConstrained)
                        continue;
                    var arc = entity as ISketchArc;
                    if (arc != null && arc.IsCircle() == 1)
                        targets.Add(new DispatchWrapper(segment));
                }
            }

            if (targets.Count == 0)
                return "当前草图没有欠定义整圆。";

            int count = model.Extension.MultiSelect2(targets.ToArray(), false, null);
            model.GraphicsRedraw2();
            return count == targets.Count
                ? string.Format("已选中 {0} 个欠定义整圆。", count)
                : string.Format("选中 {0} / {1} 个欠定义整圆。", count, targets.Count);
        }
    }
}
