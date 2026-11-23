using System;
using System.Linq;
using System.Reflection;
using SolidWorks.Interop.sldworks;

internal static class VerifyRuntime
{
    private static int Main(string[] args)
    {
        try
        {
            if (!args.Any(arg => arg.StartsWith("--pid=", StringComparison.Ordinal)))
                throw new InvalidOperationException("Specify the isolated verification process ID.");
            var app = RuntimeSession.Connect(args);
            const string progId = "SketchQuickTools.SketchToolsAddin";
            string file = System.IO.Path.Combine(@"D:\SolidWorksSketchTools", typeof(SketchQuickTools.PartNote).Assembly.GetName().Version.ToString(), "SolidWorksSketchTools.dll");
            IModelDoc2 fixture = null;
            if (app.GetDocumentCount() == 0)
                fixture = app.NewDocument(@"C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2026\templates\gb_part.prtdot", 0, 0, 0) as IModelDoc2;
            if (app.GetAddInObject(progId) == null && app.LoadAddIn(file) != 0)
                throw new InvalidOperationException("Unable to load installed 1.2 add-in");
            object instance = app.GetAddInObject(progId);
            object enable = instance.GetType().InvokeMember("CanAddPartNote", BindingFlags.InvokeMethod, null, instance, null);
            Console.WriteLine("Installed CanAddPartNote callback=" + enable);
            CheckMenus(app, true);
            if (app.UnloadAddIn(file) != 0 || app.GetAddInObject(progId) != null)
                throw new InvalidOperationException("Installed add-in did not unload");
            CheckMenus(app, false);
            if (app.LoadAddIn(file) != 0 || app.GetAddInObject(progId) == null)
                throw new InvalidOperationException("Installed add-in did not reload");
            CheckMenus(app, true);
            Console.WriteLine("PASS installed DLL loaded, unloaded and reloaded with exactly one of each command menu");
            if (fixture != null && string.IsNullOrEmpty(fixture.GetPathName()))
            {
                app.CloseDoc(fixture.GetTitle());
                if (app.GetDocumentCount() == 0)
                    app.ExitApp();
            }
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }

    private static void CheckMenus(ISldWorks app, bool loaded)
    {
        foreach (int documentType in new[] { 1, 2 })
        {
            var names = app.IFrameObject().GetSubMenus(documentType, "Tools") as object[];
            foreach (string name in new[] { "草图快捷工具", "装配体批注", "零件批注" })
            {
                int count = names == null ? 0 : names.Count(item => string.Equals(item as string, name, StringComparison.Ordinal));
                if (count != (loaded ? 1 : 0))
                    throw new InvalidOperationException(name + " count=" + count + " on document type " + documentType);
            }
        }
    }
}
