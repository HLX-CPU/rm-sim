using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace SketchQuickTools
{
    internal sealed class SketchCommands
    {
        private const int GroupId = 82041;
        private const int SelectCirclesId = 1;
        private const string GroupName = "草图快捷工具";
        private const int NoteGroupId = 82042;
        private const int AddAssemblyNoteId = 1;
        private const string NoteGroupName = "装配体批注";
        private readonly ISldWorks app;
        private readonly ICommandManager manager;
        private readonly List<KeyValuePair<int, string>> registered = new List<KeyValuePair<int, string>>();

        internal SketchCommands(ISldWorks app, ICommandManager manager)
        {
            if (manager == null)
                throw new ArgumentNullException("manager");
            this.app = app;
            this.manager = manager;
        }

        internal void Register()
        {
            RegisterGroup(GroupId, GroupName, SelectCirclesId, "Circle", "选中欠定义圆",
                "选中当前二维草图中的欠定义整圆", "SelectUnderdefinedCircles", "CanSelectUnderdefinedCircles");
            RegisterGroup(NoteGroupId, NoteGroupName, AddAssemblyNoteId, "Note", "添加零件批注",
                "在装配体中为所选零件添加带引线的文字批注", "AddAssemblyNote", "CanAddAssemblyNote");
            RegisterGroup(82043, "零件批注", 1, "Note", "添加文字批注",
                "为零件中选中的面、边或顶点添加文字批注", "AddPartNote", "CanAddPartNote");
        }

        private void RegisterGroup(int groupId, string groupName, int commandId, string iconPrefix,
            string title, string hint, string action, string enable)
        {
            RemoveStaleMenus(groupName);
            int error = 0;
            ICommandGroup group = manager.CreateCommandGroup2(groupId, groupName, groupName, hint, -1, false, ref error);
            if (group == null)
                throw new InvalidOperationException("创建命令组失败，接口返回 " + error + "。");
            registered.Add(new KeyValuePair<int, string>(groupId, groupName));

            string iconDirectory = Path.GetDirectoryName(typeof(SketchCommands).Assembly.Location);
            var sizes = new[] { 20, 32, 40, 64, 96, 128 };
            string[] icons = Array.ConvertAll(sizes, size => Path.Combine(iconDirectory, iconPrefix + size + ".png"));
            group.IconList = icons;
            group.MainIconList = icons;

            int index = group.AddCommandItem2(title, -1, hint, title, 0, action, enable, commandId,
                (int)(swCommandItemType_e.swMenuItem | swCommandItemType_e.swToolbarItem));
            if (index < 0)
                throw new InvalidOperationException("创建命令失败。");
            group.HasMenu = true;
            group.HasToolbar = true;
            if (!group.Activate())
                throw new InvalidOperationException("激活命令组失败。");
        }

        internal void Unregister()
        {
            foreach (var group in registered)
            {
                manager.RemoveCommandGroup2(group.Key, true);
                RemoveStaleMenus(group.Value);
            }
            registered.Clear();
        }

        private void RemoveStaleMenus(string groupName)
        {
            var frame = app.IFrameObject();
            if (frame == null)
                return;

            // Runtime-only removal can leave Tools menu entries in SOLIDWORKS 2026 SP0.0.
            foreach (int kind in new[] { 0, 1, 2, 3 })
            {
                var items = frame.GetSubMenus(kind, "Tools") as object[];
                int count = items == null ? 0 : items.Count(item => string.Equals(item as string, groupName, StringComparison.Ordinal));
                for (int index = 0; index < count; index++)
                {
                    if (!app.RemoveMenu(kind, groupName, ""))
                        break;
                }
            }
        }
    }
}
