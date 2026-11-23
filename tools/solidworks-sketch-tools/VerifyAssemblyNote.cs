using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using SketchQuickTools;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

internal static class VerifyAssemblyNote
{
    private static bool checkCallbacks;
    private const string TreeNote = "装配检查\r\n确认紧固件扭矩";
    private const string FaceNote = "检查此面装配间隙";
    private const string TemplateRoot = @"C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2026\templates";

    private static int Main(string[] args)
    {
        try
        {
            var app = RuntimeSession.Connect(args);
            checkCallbacks = args.Contains("--callbacks");
            Verify(app, args[0], args.Contains("--ui"), args.Contains("--part"), args.Contains("--annotated"));
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + " " + error.Message);
            return 1;
        }
    }

    private static void Verify(ISldWorks app, string output, bool ui, bool testPart, bool annotated)
    {
        var previous = app.ActiveDoc as IModelDoc2;
        bool previousDirty = previous != null && previous.GetSaveFlag();
        string originalTitle = previous == null ? null : previous.GetTitle();
        string partPath = Path.Combine(output, "RemarkTarget.SLDPRT");
        string assemblyPath = Path.Combine(output, "AssemblyNoteFixture.SLDASM");
        string partTitle = null, assemblyTitle = null;
        bool retained = false;
        try
        {
            var part = (IModelDoc2)app.NewDocument(Path.Combine(TemplateRoot, "gb_part.prtdot"), 0, 0, 0);
            Require(part != null && (previous == null || app.IsSame(part, previous) != 1) &&
                string.IsNullOrEmpty(part.GetPathName()), "No separate temporary part");
            partTitle = part.GetTitle();
            IFeature plane = part.FirstFeature() as IFeature;
            while (plane != null && plane.GetTypeName2() != "RefPlane")
                plane = plane.GetNextFeature() as IFeature;
            Require(plane != null && plane.Select2(false, 0), "Fixture plane selection failed");
            part.SketchManager.InsertSketch(true);
            part.SketchManager.CreateCornerRectangle(-0.025, -0.017, 0, 0.025, 0.017, 0);
            var extrusion = part.FeatureManager.FeatureExtrusion2(true, false, false, 0, 0, 0.014, 0.014,
                false, false, false, false, 0, 0, false, false, false, false, true, true, true, 0, 0, false);
            Require(extrusion != null, "Fixture extrusion failed");
            Save(part, partPath);
            partTitle = part.GetTitle();
            Require(!part.GetSaveFlag(), "Temporary part still dirty after save");
            if (testPart)
            {
                if (ui)
                {
                    var solid = (IBody2)((object[])((IPartDoc)part).GetBodies2(0, false))[0];
                    ((IEntity)((object[])solid.GetFaces())[0]).Select4(false, null);
                    part.ShowNamedView2("*Isometric", (int)swStandardViews_e.swIsometricView);
                    part.ViewZoomtofit2();
                    if (annotated)
                    {
                        CheckPart(part, PartNote.Insert(app, part, "零件批注测试\r\n此处去毛刺"), "零件批注测试\r\n此处去毛刺", part.Extension.GetPersistReference3(((ISelectionMgr)part.SelectionManager).GetSelectedObject6(1, -1)));
                        Save(part, partPath);
                    }
                    retained = true;
                    Console.WriteLine("UI part fixture " + partPath);
                    return;
                }
                VerifyPartNotes(app, part, partPath);
                return;
            }

            var model = (IModelDoc2)app.NewDocument(Path.Combine(TemplateRoot, "gb_assembly.asmdot"), 0, 0, 0);
            Require(model != null && (previous == null || app.IsSame(model, previous) != 1) &&
                string.IsNullOrEmpty(model.GetPathName()), "No separate temporary assembly");
            assemblyTitle = model.GetTitle();
            var assembly = (IAssemblyDoc)model;
            var first = assembly.AddComponent5(partPath, 0, "", false, "", -0.06, 0, 0);
            var second = assembly.AddComponent5(partPath, 0, "", false, "", 0.06, 0, 0);
            Require(first != null && second != null && first.Name2 != second.Name2, "Could not create two distinct component occurrences");
            string firstName = first.Name2, secondName = second.Name2;
            model.ShowNamedView2("*Isometric", (int)swStandardViews_e.swIsometricView);
            model.ViewZoomtofit2();
            model.ClearSelection2(true);
            Require(!AssemblyNote.CanInsert(app, model), "Command enabled without selection");
            if (checkCallbacks) RuntimeSession.VerifyCommand(app, "CanAddAssemblyNote", 0);
            Require(first.Select4(false, null, false) && second.Select4(true, null, false), "Could not select both components");
            Require(!AssemblyNote.CanInsert(app, model), "Command enabled for multiple components");
            Require(second.Select4(false, null, false), "Could not select the second component");
            Require(AssemblyNote.CanInsert(app, model), "Command disabled on one selected component");
            if (checkCallbacks) RuntimeSession.VerifyCommand(app, "CanAddAssemblyNote", 1);
            if (ui)
            {
                Save(model, assemblyPath);
                retained = true;
                Console.WriteLine("UI fixture " + assemblyPath + ", selected occurrence " + secondName);
                return;
            }

            Require(AssemblyNote.Insert(app, model, " \r\n") == null && CountNotes(model) == 0,
                "Blank input created an annotation");
            Check(AssemblyNote.Insert(app, model, TreeNote), TreeNote, secondName);
            object info;
            var body = (IBody2)((object[])first.GetBodies3((int)swBodyType_e.swSolidBody, out info))[0];
            var face = (IEntity)((object[])body.GetFaces())[0];
            Require(face.Select4(false, null), "Could not select a component face");
            Check(AssemblyNote.Insert(app, model, FaceNote), FaceNote, firstName);
            Require(!part.GetSaveFlag(), "Creating assembly notes dirtied the referenced part");
            Console.WriteLine("PASS Chinese multiline note, native leaders, selected component and selected face, distinct attachments, no part edit");

            Save(model, assemblyPath);
            assemblyTitle = model.GetTitle();
            app.CloseDoc(assemblyTitle);
            assemblyTitle = null;
            int errors = 0, warnings = 0;
            model = (IModelDoc2)app.OpenDoc6(assemblyPath, (int)swDocumentTypes_e.swDocASSEMBLY,
                (int)swOpenDocOptions_e.swOpenDocOptions_Silent, "", ref errors, ref warnings);
            Require(model != null && errors == 0, "Fixture reload failed: " + errors);
            assemblyTitle = model.GetTitle();
            var notes = GetNotes(model);
            Require(notes.Length == 2, "Expected two persisted assembly notes");
            Check(notes.Single(note => Normalize(note.GetText()) == Normalize(TreeNote)), TreeNote, secondName);
            Check(notes.Single(note => Normalize(note.GetText()) == Normalize(FaceNote)), FaceNote, firstName);
            Console.WriteLine("PASS saved and reopened assembly kept both notes and exact occurrence attachments");
        }
        finally
        {
            if (!retained)
            {
                if (assemblyTitle != null)
                    app.CloseDoc(assemblyTitle);
                if (partTitle != null && app.GetOpenDocumentByName(partPath) != null)
                    app.CloseDoc(partTitle);
                if (originalTitle != null && app.IsSame(app.ActiveDoc, previous) != 1)
                {
                    int activation = 0;
                    app.ActivateDoc3(originalTitle, false, (int)swRebuildOnActivation_e.swDontRebuildActiveDoc, ref activation);
                }
                Require(previous == null || (app.IsSame(app.ActiveDoc, previous) == 1 && previous.GetSaveFlag() == previousDirty),
                    "Previous document state changed");
            }
        }
    }

    private static void VerifyPartNotes(ISldWorks app, IModelDoc2 part, string path)
    {
        var body = (IBody2)((object[])((IPartDoc)part).GetBodies2(0, false))[0];
        var targets = new[] {
            (IEntity)((object[])body.GetFaces())[0],
            (IEntity)((object[])body.GetEdges())[0],
            (IEntity)((object[])body.GetVertices())[0]
        };
        var texts = new[] { "零件表面检查\r\n去毛刺", "此边倒钝", "定位顶点" };
        var refs = targets.Select(t => part.Extension.GetPersistReference3(t)).ToArray();
        part.ShowNamedView2("*Isometric", (int)swStandardViews_e.swIsometricView);
        string geometry = string.Join(",", ((double[])body.GetBodyBox()).Select(v => v.ToString("R", System.Globalization.CultureInfo.InvariantCulture)));
        part.ClearSelection2(true);
        Require(!PartNote.CanInsert(part), "Part command enabled without a target");
        if (checkCallbacks) RuntimeSession.VerifyCommand(app, "CanAddPartNote", 0);
        Require(targets[0].Select4(false, null) && targets[1].Select4(true, null), "Multiple selection setup failed");
        Require(!PartNote.CanInsert(part), "Part command enabled for multiple targets");
        Require(targets[0].Select4(false, null), "Face selection failed");
        Require(PartNote.Insert(app, part, " \r\n") == null && CountNotes(part) == 0 && !part.GetSaveFlag(), "Blank input changed the part");
        for (int i = 0; i < targets.Length; i++)
        {
            Require(targets[i].Select4(false, null) && PartNote.CanInsert(part), "Part command unavailable for target " + i);
            if (checkCallbacks) RuntimeSession.VerifyCommand(app, "CanAddPartNote", 1);
            CheckPart(part, PartNote.Insert(app, part, texts[i]), texts[i], refs[i]);
        }
        Require(CountNotes(part) == 3, "Unexpected part annotation count");
        Require(geometry == string.Join(",", ((double[])body.GetBodyBox()).Select(v => v.ToString("R", System.Globalization.CultureInfo.InvariantCulture))), "Part body changed");
        Console.WriteLine("PASS part face, edge, vertex, Chinese multiline text, native attached leaders, blank/multiple/no selection and body geometry");
        Save(part, path);
        app.CloseDoc(part.GetTitle());
        int errors = 0, warnings = 0;
        part = (IModelDoc2)app.OpenDoc6(path, (int)swDocumentTypes_e.swDocPART, (int)swOpenDocOptions_e.swOpenDocOptions_Silent, "", ref errors, ref warnings);
        Require(part != null && errors == 0, "Part reopen failed " + errors);
        var notes = GetNotes(part);
        Require(notes.Length == 3, "Part notes did not persist");
        for (int i = 0; i < texts.Length; i++)
            CheckPart(part, notes.Single(n => Normalize(n.GetText()) == Normalize(texts[i])), texts[i], refs[i]);
        Console.WriteLine("PASS saved/reopened SLDPRT retains three exact texts and face/edge/vertex attachments");
    }

    private static void CheckPart(IModelDoc2 part, INote note, string text, object reference)
    {
        Require(note != null && Normalize(note.GetText()) == Normalize(text), "Part note text differs");
        var annotation = (IAnnotation)note.GetAnnotation();
        var attached = annotation.GetAttachedEntities3() as object[];
        Require(annotation.GetLeaderCount() == 1 && annotation.GetLeaderStyle() != (int)swLeaderStyle_e.swNO_LEADER && attached != null && attached.Length == 1, "Part note lacks one attached leader");
        Require(part.Extension.IsSamePersistentID(reference, part.Extension.GetPersistReference3(attached[0])) == 1, "Part note attached to the wrong entity");
    }

    private static INote[] GetNotes(IModelDoc2 model)
    {
        var notes = new System.Collections.Generic.List<INote>();
        var annotation = model.GetFirstAnnotation2() as IAnnotation;
        while (annotation != null)
        {
            var note = annotation.GetSpecificAnnotation() as INote;
            if (note != null)
                notes.Add(note);
            annotation = annotation.GetNext3() as IAnnotation;
        }
        return notes.ToArray();
    }

    private static int CountNotes(IModelDoc2 model) { return GetNotes(model).Length; }

    private static void Check(INote note, string text, string component)
    {
        Require(note != null && Normalize(note.GetText()) == Normalize(text), "Note text differs");
        var annotation = (IAnnotation)note.GetAnnotation();
        Require(annotation.GetLeaderCount() == 1 && annotation.GetLeaderStyle() != (int)swLeaderStyle_e.swNO_LEADER,
            "Note lacks a visible leader");
        var attached = annotation.GetAttachedEntities3() as object[];
        Require(attached != null && attached.Length == 1, "Expected one attached entity");
        var owner = ((IEntity)attached[0]).GetComponent() as IComponent2;
        Require(owner != null && owner.Name2 == component, "Leader bound to wrong component occurrence");
    }

    private static string Normalize(string value) { return value.Replace("\r\n", "\n"); }

    private static void Save(IModelDoc2 model, string path)
    {
        int errors = 0, warnings = 0;
        Require(model.Extension.SaveAs(path, 0, (int)swSaveAsOptions_e.swSaveAsOptions_Silent, null, ref errors, ref warnings)
            && errors == 0, "Fixture save failed: " + errors);
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}
