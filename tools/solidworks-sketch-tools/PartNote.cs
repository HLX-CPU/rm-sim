using System;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace SketchQuickTools
{
    public static class PartNote
    {
        internal static IEntity GetTarget(IModelDoc2 model)
        {
            if (model == null || model.GetType() != (int)swDocumentTypes_e.swDocPART ||
                model.IsOpenedReadOnly() || model.IsOpenedViewOnly() || model.SketchManager.ActiveSketch != null)
                return null;
            var selection = (ISelectionMgr)model.SelectionManager;
            if (selection.GetSelectedObjectCount2(-1) != 1)
                return null;
            object item = selection.GetSelectedObject6(1, -1);
            return item is IFace2 || item is IEdge || item is IVertex ? item as IEntity : null;
        }

        public static bool CanInsert(IModelDoc2 model)
        {
            return GetTarget(model) != null;
        }

        public static INote Insert(ISldWorks app, IModelDoc2 model, string text)
        {
            if (string.IsNullOrWhiteSpace(text))
                return null;
            IEntity target = GetTarget(model);
            if (target == null)
                throw new InvalidOperationException("请先退出草图编辑，在零件窗口选中一个面、边或顶点。");
            object reference = model.Extension.GetPersistReference3(target);
            INote note = null;
            try
            {
                note = model.InsertNote(text) as INote;
                if (note == null)
                    throw new InvalidOperationException("SOLIDWORKS 未返回新建注释。");
                var annotation = note.GetAnnotation() as IAnnotation;
                var attached = annotation == null ? null : annotation.GetAttachedEntities3() as object[];
                if (annotation == null || annotation.GetLeaderCount() == 0 ||
                    annotation.GetLeaderStyle() == (int)swLeaderStyle_e.swNO_LEADER ||
                    attached == null || attached.Length != 1 ||
                    model.Extension.IsSamePersistentID(reference, model.Extension.GetPersistReference3(attached[0])) != 1)
                    throw new InvalidOperationException("SOLIDWORKS 未将引线附着到所选几何体。");
                note.Visible = true;
                annotation.Visible = (int)swAnnotationVisibilityState_e.swAnnotationVisible;
                if (annotation.AnnotationView != null)
                {
                    annotation.AnnotationView.Show();
                    annotation.AnnotationView.ActivateAndReorient();
                }
                PlaceOutsideBody(app, model, annotation);
                model.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swDisplayAnnotations, true);
                model.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swDisplayNotes, true);
                model.IActiveView.ZoomByFactor(0.8);
                return note;
            }
            catch
            {
                if (note != null && !AssemblyNote.RemoveCreatedNote(model, note))
                    throw new InvalidOperationException("引线附着验证失败，刚创建的注释仍可在零件里撤销。");
                throw;
            }
            finally
            {
                target.Select4(false, null);
                model.GraphicsRedraw2();
            }
        }

        private static void PlaceOutsideBody(ISldWorks app, IModelDoc2 model, IAnnotation annotation)
        {
            var bounds = ((IPartDoc)model).GetPartBox(true) as double[];
            var view = model.IActiveView;
            if (bounds == null || bounds.Length != 6 || view == null)
                throw new InvalidOperationException("无法确定批注放置位置。");
            var math = (IMathUtility)app.GetMathUtility();
            var rotation = view.Orientation3;
            double minX = double.PositiveInfinity, minY = minX;
            double maxX = double.NegativeInfinity, maxY = maxX, maxZ = maxX;
            for (int corner = 0; corner < 8; corner++)
            {
                var point = (IMathPoint)math.CreatePoint(new[] {
                    bounds[(corner & 1) == 0 ? 0 : 3],
                    bounds[(corner & 2) == 0 ? 1 : 4],
                    bounds[(corner & 4) == 0 ? 2 : 5]
                });
                var screen = (double[])((IMathPoint)point.MultiplyTransform(rotation)).ArrayData;
                minX = Math.Min(minX, screen[0]);
                maxX = Math.Max(maxX, screen[0]);
                minY = Math.Min(minY, screen[1]);
                maxY = Math.Max(maxY, screen[1]);
                maxZ = Math.Max(maxZ, screen[2]);
            }
            double gap = Math.Max(0.0035, Math.Max(maxX - minX, maxY - minY) * 0.12);
            var location = (IMathPoint)math.CreatePoint(new[] { minX, minY - gap, maxZ + gap });
            var position = (double[])((IMathPoint)location.MultiplyTransform(rotation.IInverse())).ArrayData;
            if (!annotation.SetPosition2(position[0], position[1], position[2]))
                throw new InvalidOperationException("SOLIDWORKS 未能放置批注。");
        }
    }
}
