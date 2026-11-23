using System;
using System.Reflection;
using System.Runtime.InteropServices;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swpublished;

[assembly: AssemblyVersion("1.2.1.0")]

namespace SketchQuickTools
{
    [ComVisible(true)]
    [ClassInterface(ClassInterfaceType.AutoDispatch)]
    [Guid("3A3E2748-FE45-4ECD-B56B-E0152DFA085A")]
    [ProgId("SketchQuickTools.SketchToolsAddin")]
    public sealed class SketchToolsAddin : ISwAddin
    {
        private ISldWorks app;
        private SketchCommands commands;

        public bool ConnectToSW(object application, int cookie)
        {
            app = application as ISldWorks;
            if (app == null)
                return false;

            try
            {
                if (!app.SetAddinCallbackInfo2(0, this, cookie))
                    throw new InvalidOperationException("无法注册加载项回调。");
                commands = new SketchCommands(app, app.GetCommandManager(cookie));
                commands.Register();
                return true;
            }
            catch (Exception error)
            {
                SetStatus("草图快捷工具加载失败。" + error.Message);
                DisconnectFromSW();
                return false;
            }
        }

        public bool DisconnectFromSW()
        {
            try
            {
                if (commands != null)
                    commands.Unregister();
                return true;
            }
            catch (COMException error)
            {
                SetStatus("草图快捷工具卸载失败。" + error.Message);
                return false;
            }
            finally
            {
                commands = null;
                app = null;
            }
        }

        public int CanSelectUnderdefinedCircles()
        {
            try
            {
                return app != null && CircleSelection.GetActiveSketch(app, app.ActiveDoc as IModelDoc2) != null ? 1 : 0;
            }
            catch (COMException)
            {
                return 0;
            }
        }

        public void SelectUnderdefinedCircles()
        {
            try
            {
                if (app != null)
                    SetStatus(CircleSelection.Select(app, app.ActiveDoc as IModelDoc2));
            }
            catch (COMException error)
            {
                SetStatus("选圆失败。" + error.Message);
            }
        }

        public int CanAddAssemblyNote()
        {
            try
            {
                return app != null && AssemblyNote.CanInsert(app, app.ActiveDoc as IModelDoc2) ? 1 : 0;
            }
            catch (COMException)
            {
                return 0;
            }
        }

        public void AddAssemblyNote()
        {
            AddNote(false);
        }

        public int CanAddPartNote()
        {
            try
            {
                return app != null && PartNote.CanInsert(app.ActiveDoc as IModelDoc2) ? 1 : 0;
            }
            catch (COMException)
            {
                return 0;
            }
        }

        public void AddPartNote()
        {
            AddNote(true);
        }

        private void AddNote(bool inPart)
        {
            if (app == null)
                return;
            try
            {
                var model = app.ActiveDoc as IModelDoc2;
                var component = inPart ? null : AssemblyNote.GetComponent(app, model);
                var target = inPart ? PartNote.GetTarget(model) : null;
                if (inPart ? target == null : component == null)
                {
                    SetStatus(inPart ? "请在零件中选中一个面、边或顶点。" : "请先选中装配体中的一个零件。");
                    return;
                }

                string name = inPart ? model.GetTitle() : component.Name2;
                object reference = inPart ? model.Extension.GetPersistReference3(target) : null;
                var frame = app.IFrameObject();
                string text = NoteDialog.Read(frame == null ? IntPtr.Zero : new IntPtr(frame.GetHWndx64()), name);
                if (text == null)
                    return;

                if (app.IsSame(model, app.ActiveDoc) != (int)SolidWorks.Interop.swconst.swObjectEquality.swObjectSame)
                    throw new InvalidOperationException("活动文档已切换，请重新选中批注对象。");
                if (inPart)
                {
                    var current = PartNote.GetTarget(model);
                    if (current == null || model.Extension.IsSamePersistentID(reference, model.Extension.GetPersistReference3(current)) != 1)
                        throw new InvalidOperationException("几何体选择已变化，请重新选择。");
                    PartNote.Insert(app, model, text);
                }
                else
                {
                    var current = AssemblyNote.GetComponent(app, model);
                    if (current == null || !string.Equals(name, current.Name2, StringComparison.Ordinal))
                        throw new InvalidOperationException("零件选择已变化，请重新选择。");
                    AssemblyNote.Insert(app, model, text);
                }
                SetStatus("已添加批注。保存当前文档以保留批注。");
            }
            catch (COMException error)
            {
                SetStatus("添加批注失败。" + error.Message);
            }
            catch (InvalidOperationException error)
            {
                SetStatus(error.Message);
            }
        }

        private void SetStatus(string message)
        {
            if (app == null)
                return;
            try
            {
                var frame = app.IFrameObject();
                if (frame != null)
                    frame.SetStatusBarText(message);
            }
            catch (COMException)
            {
                // SOLIDWORKS can close the frame before unloading the add-in.
            }
        }
    }
}
