using System;
using System.Runtime.InteropServices;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace SketchQuickTools
{
    public static class AssemblyNote
    {
        public static bool CanInsert(ISldWorks app, IModelDoc2 model)
        {
            return GetComponent(app, model) != null;
        }

        internal static IComponent2 GetComponent(ISldWorks app, IModelDoc2 model)
        {
            if (model == null || model.GetType() != (int)swDocumentTypes_e.swDocASSEMBLY ||
                model.IsOpenedReadOnly() || model.IsOpenedViewOnly() || model.SketchManager.ActiveSketch != null)
                return null;

            var assembly = (IAssemblyDoc)model;
            object editTarget = assembly.GetEditTarget();
            if (editTarget != null && app.IsSame(model, editTarget) != (int)swObjectEquality.swObjectSame)
                return null;

            var selection = (ISelectionMgr)model.SelectionManager;
            if (selection.GetSelectedObjectCount2(-1) != 1)
                return null;
            object selected = selection.GetSelectedObject6(1, -1);
            var component = selected as IComponent2;
            if (component == null && (selected is IFace2 || selected is IEdge || selected is IVertex))
                component = selection.GetSelectedObjectsComponent4(1, -1) as IComponent2;

            if (component == null || component.IsSuppressed() || component.IsHidden(false))
                return null;
            var part = component.GetModelDoc2() as IModelDoc2;
            return part != null && part.GetType() == (int)swDocumentTypes_e.swDocPART ? component : null;
        }

        public static INote Insert(ISldWorks app, IModelDoc2 model, string text)
        {
            if (string.IsNullOrWhiteSpace(text))
                return null;
            IComponent2 component = GetComponent(app, model);
            if (component == null)
                throw new InvalidOperationException("请退出零件或草图编辑，并选中装配体中的一个可见零件或其面、边、顶点。");

            var selection = (ISelectionMgr)model.SelectionManager;
            object selected = selection.GetSelectedObject6(1, -1);
            var picked = selected is IComponent2 ? null : selected as IEntity;
            INote note = null;
            try
            {
                if (picked == null)
                {
                    IEntity face = FindAnchorFace(app, model, component);
                    if (face == null || !face.Select4(false, null))
                        throw new InvalidOperationException("找不到可连接引线的零件表面。可直接选中一个面后重试。");
                }

                note = model.InsertNote(text) as INote;
                if (note == null)
                    throw new InvalidOperationException("SOLIDWORKS 未返回新建注释。");
                var annotation = note.GetAnnotation() as IAnnotation;
                if (annotation == null || !IsAttachedTo(annotation, component))
                    throw new InvalidOperationException("SOLIDWORKS 未将引线附着到所选零件。");
                return note;
            }
            catch
            {
                if (note != null && !RemoveCreatedNote(model, note))
                    throw new InvalidOperationException("引线附着验证失败，刚创建的注释仍可在装配体里撤销。");
                throw;
            }
            finally
            {
                model.ClearSelection2(true);
                if (picked == null)
                    component.Select4(false, null, false);
                else
                    picked.Select4(false, null);
                model.GraphicsRedraw2();
            }
        }

        private static IEntity FindAnchorFace(ISldWorks app, IModelDoc2 model, IComponent2 component)
        {
            var view = model.IActiveView;
            var orientation = view == null ? null : view.Orientation3;
            var transform = component.Transform2;
            var math = (IMathUtility)app.GetMathUtility();
            IFace2 best = null;
            double bestFacing = double.NegativeInfinity, bestArea = 0;

            foreach (int kind in new[] { (int)swBodyType_e.swSolidBody, (int)swBodyType_e.swSheetBody })
            {
                object info;
                var bodies = component.GetBodies3(kind, out info) as object[];
                if (bodies == null)
                    continue;
                foreach (object obj in bodies)
                {
                    var body = obj as IBody2;
                    var faces = body == null ? null : body.GetFaces() as object[];
                    if (faces == null)
                        continue;
                    foreach (object item in faces)
                    {
                        var face = item as IFace2;
                        if (face == null)
                            continue;
                        var entity = (IEntity)face;
                        var owner = entity.GetComponent() as IComponent2;
                        if (owner == null || !string.Equals(owner.Name2, component.Name2, StringComparison.Ordinal))
                            continue;
                        double area = face.GetArea();
                        double facing = Facing(math, face, transform, orientation);
                        if (best == null || facing > bestFacing + 0.001 ||
                            (Math.Abs(facing - bestFacing) <= 0.001 && area > bestArea))
                        {
                            best = face;
                            bestFacing = facing;
                            bestArea = area;
                        }
                    }
                }
            }
            return best as IEntity;
        }

        private static double Facing(IMathUtility math, IFace2 face, IMathTransform component, IMathTransform view)
        {
            var normal = face.Normal as double[];
            if (normal == null || normal.Length != 3 || normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2] < 0.01)
                return 0;
            var vector = (IMathVector)math.CreateVector(normal);
            if (component != null)
                vector = (IMathVector)vector.MultiplyTransform(component);
            if (view != null)
                vector = (IMathVector)vector.MultiplyTransform(view);
            return ((double[])vector.ArrayData)[2];
        }

        private static bool IsAttachedTo(IAnnotation annotation, IComponent2 component)
        {
            var attachments = annotation.GetAttachedEntities3() as object[];
            if (annotation.GetLeaderCount() == 0 || annotation.GetLeaderStyle() == (int)swLeaderStyle_e.swNO_LEADER ||
                attachments == null || attachments.Length != 1)
                return false;
            var entity = attachments[0] as IEntity;
            var owner = entity == null ? null : entity.GetComponent() as IComponent2;
            return owner != null && string.Equals(owner.Name2, component.Name2, StringComparison.Ordinal);
        }

        internal static bool RemoveCreatedNote(IModelDoc2 model, INote note)
        {
            try
            {
                model.ClearSelection2(true);
                var annotation = (IAnnotation)note.GetAnnotation();
                return annotation != null && annotation.Select3(false, null) && model.Extension.DeleteSelection2(0);
            }
            catch (COMException)
            {
                return false;
            }
        }
    }
}
