using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Threading;
using System.Reflection;
using SolidWorks.Interop.sldworks;

internal static class RuntimeSession
{
    internal static void VerifyCommand(ISldWorks app, string method, int expected)
    {
        var instance = app.GetAddInObject("SketchQuickTools.SketchToolsAddin");
        if (instance == null)
            throw new InvalidOperationException("The installed add-in is not loaded.");
        object value = instance.GetType().InvokeMember(method, BindingFlags.InvokeMethod, null, instance, null);
        if (Convert.ToInt32(value) != expected)
            throw new InvalidOperationException(method + " returned " + value + ", expected " + expected);
        Console.WriteLine("PASS installed " + method + "=" + expected);
    }

    [DllImport("ole32.dll")] private static extern int GetRunningObjectTable(int reserved, out IRunningObjectTable table);
    [DllImport("ole32.dll")] private static extern int CreateBindCtx(int reserved, out IBindCtx context);

    internal static ISldWorks Connect(string[] args)
    {
        string requested = Array.Find(args, arg => arg.StartsWith("--pid=", StringComparison.Ordinal));
        if (requested == null)
            return (ISldWorks)Marshal.GetActiveObject("SldWorks.Application");
        int pid = int.Parse(requested.Substring(6));
        for (int attempt = 0; attempt < 40; attempt++)
        {
            var app = Find(pid);
            if (app != null)
                return app;
            Thread.Sleep(1000);
        }
        throw new InvalidOperationException("The requested SOLIDWORKS session is not registered: " + pid);
    }

    private static ISldWorks Find(int pid)
    {
        IRunningObjectTable table;
        GetRunningObjectTable(0, out table);
        IEnumMoniker names;
        table.EnumRunning(out names);
        IBindCtx context;
        CreateBindCtx(0, out context);
        var one = new IMoniker[1];
        try
        {
            while (names.Next(1, one, IntPtr.Zero) == 0)
            {
                try
                {
                    string name;
                    one[0].GetDisplayName(context, null, out name);
                    if (!name.StartsWith("SolidWorks", StringComparison.OrdinalIgnoreCase) ||
                        name.IndexOf(pid.ToString(), StringComparison.Ordinal) < 0)
                        continue;
                    object value;
                    table.GetObject(one[0], out value);
                    var app = value as ISldWorks;
                    if (app != null && app.GetProcessID() == pid)
                        return app;
                }
                catch (COMException) { }
                finally { Marshal.ReleaseComObject(one[0]); }
            }
        }
        finally
        {
            Marshal.ReleaseComObject(context);
            Marshal.ReleaseComObject(names);
            Marshal.ReleaseComObject(table);
        }
        return null;
    }
}
