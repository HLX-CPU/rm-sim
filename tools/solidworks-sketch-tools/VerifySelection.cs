using System;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices;
using SketchQuickTools;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

internal static class VerifySelection
{
    private static bool checkCallbacks;
    private static int Main(string[] args)
    {
        try
        {
            var app = RuntimeSession.Connect(args);
            checkCallbacks = args.Contains("--callbacks");
            var previous = app.ActiveDoc as IModelDoc2;
            bool dirty = previous != null && previous.GetSaveFlag();
            bool prepareUi = args.Length == 1 && args[0] == "--ui";
            RunFixture(app, previous, (int)swUserPreferenceStringValue_e.swDefaultTemplatePart, true, prepareUi);
            if (prepareUi)
                return 0;
            RunFixture(app, previous, (int)swUserPreferenceStringValue_e.swDefaultTemplateAssembly, false, false);
            Require(previous == null || (app.IsSame(app.ActiveDoc, previous) == 1 && previous.GetSaveFlag() == dirty),
                "Original active document changed");
            Console.WriteLine("PASS original document restored with original save flag");
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static void RunFixture(ISldWorks app, IModelDoc2 previous, int templatePreference, bool mixed, bool prepareUi)
    {
        string fallback = mixed ? "gb_part.prtdot" : "gb_assembly.asmdot";
        string template = app.GetUserPreferenceStringValue(templatePreference);
        if (!System.IO.File.Exists(template))
            template = System.IO.Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.CommonApplicationData),
                @"SOLIDWORKS\SOLIDWORKS 2026\templates", fallback);
        var doc = app.NewDocument(template, 0, 0, 0) as IModelDoc2;
        Require(doc != null && (previous == null || app.IsSame(previous, doc) != 1), "No distinct disposable document");
        Require(string.IsNullOrEmpty(doc.GetPathName()), "Fixture is not an unsaved new document");
        bool retained = false;
        try
        {
            IFeature plane = (IFeature)doc.FirstFeature();
            while (plane != null && plane.GetTypeName2() != "RefPlane")
                plane = (IFeature)plane.GetNextFeature();
            Require(plane != null && plane.Select2(false, 0), "Could not select a plane");
            var manager = doc.SketchManager;
            manager.InsertSketch(true);
            Require(manager.ActiveSketch != null && !manager.ActiveSketch.Is3D(), "Fixture is not editing a 2D sketch");
            doc.ClearSelection2(true);
            CircleSelection.Select(app, doc);
            Require(Selected(doc).Length == 0, "Empty sketch selection changed");

            manager.AddToDB = true;
            ISketchSegment free, construction, sized = null, fixedCircle = null, arc = null, line = null;
            try
            {
                free = manager.CreateCircleByRadius(-0.034, 0.031, 0, 0.005);
                construction = manager.CreateCircleByRadius(-0.013, 0.029, 0, 0.0045);
                construction.ConstructionGeometry = true;
                if (mixed)
                {
                    sized = manager.CreateCircleByRadius(0.012, 0.027, 0, 0.005);
                    fixedCircle = manager.CreateCircleByRadius(0.035, 0.025, 0, 0.006);
                    arc = manager.CreateArc(0.03, -0.015, 0, 0.037, -0.015, 0, 0.03, -0.008, 0, 1);
                    line = manager.CreateLine(-0.03, -0.02, 0, 0.014, -0.035, 0);
                }
            }
            finally { manager.AddToDB = false; }

            IDisplayDimension diameter = null;
            if (mixed)
            {
                Fix(doc, fixedCircle);
                Require(fixedCircle.Status == (int)swConstrainedStatus_e.swFullyConstrained, "Fixture circle is not fully constrained");
                diameter = AddDiameter(app, doc, sized);
                Require(sized.Status == (int)swConstrainedStatus_e.swUnderConstrained, "Fixture diameter unexpectedly defined its center");
                Require(line.Select4(false, null), "Could not preselect the line");
            }
            ISketchSegment[] expected = mixed ? new[] { free, construction, sized } : new[] { free, construction };
            ISketchSegment[] everySegment = mixed ? new[] { free, construction, sized, fixedCircle, arc, line } : expected;
            if (prepareUi)
            {
                doc.SetTitle2("SketchQuickToolsUiFixture");
                doc.ShowNamedView2("*Front", (int)swStandardViews_e.swFrontView);
                doc.ViewZoomtofit2();
                Console.WriteLine("UI fixture: " + doc.GetTitle());
                Console.WriteLine("Expected selected segment IDs: " + string.Join(",", expected.Select(Key)));
                retained = true;
                return;
            }
            string before = Snapshot(doc, everySegment, diameter);
            if (checkCallbacks) RuntimeSession.VerifyCommand(app, "CanSelectUnderdefinedCircles", 1);
            string message = CircleSelection.Select(app, doc);
            Require(Selected(doc).SequenceEqual(expected.Select(Key).OrderBy(key => key)),
                "Selected entities differ from expected: " + message + "; statuses " + string.Join(",", expected.Select(item => item.Status)));
            Require(before == Snapshot(doc, everySegment, diameter), "Selection changed sketch state");
            Console.WriteLine(mixed
                ? "PASS part: free, construction and existing-diameter circles selected; fixed, arc and line excluded"
                : "PASS assembly: both underdefined circles selected");

            if (mixed)
            {
                doc.ClearSelection2(true);
                manager.InsertSketch(true);
                Require(plane.Select2(false, 0), "Could not start the no-match sketch");
                manager.InsertSketch(true);
                var control = manager.CreateLine(-0.03, -0.02, 0, 0.014, -0.035, 0);
                Require(control.Select4(false, null), "Could not preselect a control line");
                string[] prior = Selected(doc);
                CircleSelection.Select(app, doc);
                Require(prior.Length == 1 && prior.SequenceEqual(Selected(doc)), "No-match action changed existing selection");
                doc.ClearSelection2(true);
                manager.InsertSketch(true);
                manager.Insert3DSketch(true);
                Require(manager.ActiveSketch != null && manager.ActiveSketch.Is3D(), "Could not create a 3D fixture");
                var line3d = manager.CreateLine(0.015, 0.025, 0.01, 0.016, 0.027, 0.03);
                Require(line3d.Select4(false, null), "Could not select the 3D line");
                prior = Selected(doc);
                CircleSelection.Select(app, doc);
                Require(prior.Length == 1 && prior.SequenceEqual(Selected(doc)), "3D sketch selection changed");
                Console.WriteLine("PASS no-match and 3D cases preserve selection");
            }
        }
        finally
        {
            if (!retained)
            {
                app.CloseDoc(doc.GetTitle());
                if (previous != null && app.IsSame(previous, app.ActiveDoc) != 1)
                {
                    int error = 0;
                    app.ActivateDoc3(previous.GetTitle(), false, (int)swRebuildOnActivation_e.swDontRebuildActiveDoc, ref error);
                }
            }
        }
    }

    private static void Fix(IModelDoc2 doc, ISketchSegment segment)
    {
        Require(segment.Select4(false, null), "Could not select a fixture segment");
        doc.SketchAddConstraints("sgFIXED");
        doc.ClearSelection2(true);
    }

    private static IDisplayDimension AddDiameter(ISldWorks app, IModelDoc2 doc, ISketchSegment segment)
    {
        const int toggle = (int)swUserPreferenceToggle_e.swInputDimValOnCreate;
        bool old = app.GetUserPreferenceToggle(toggle);
        try
        {
            app.SetUserPreferenceToggle(toggle, false);
            Require(segment.Select4(false, null), "Could not select the sized fixture circle");
            var sketch = doc.SketchManager.ActiveSketch;
            var math = (IMathUtility)app.GetMathUtility();
            var point = (IMathPoint)math.CreatePoint(new double[] { 0.019, 0.037, 0 });
            var modelPoint = (double[])((IMathPoint)point.MultiplyTransform(sketch.ModelToSketchTransform.IInverse())).ArrayData;
            var display = doc.AddDiameterDimension2(modelPoint[0], modelPoint[1], modelPoint[2]) as IDisplayDimension;
            Require(display != null, "Fixture diameter creation failed");
            return display;
        }
        finally
        {
            app.SetUserPreferenceToggle(toggle, old);
            doc.ClearSelection2(true);
        }
    }

    private static string Snapshot(IModelDoc2 doc, ISketchSegment[] segments, IDisplayDimension diameter)
    {
        return doc.GetUpdateStamp() + "|" + string.Join("|", segments.Select(segment =>
        {
            var arc = segment as ISketchArc;
            var line = segment as ISketchLine;
            string geometry = arc != null
                ? Coordinates((ISketchPoint)arc.GetCenterPoint2()) + "," + arc.GetRadius().ToString("R", CultureInfo.InvariantCulture)
                : Coordinates((ISketchPoint)line.GetStartPoint2()) + "," + Coordinates((ISketchPoint)line.GetEndPoint2());
            return Key(segment) + ":" + geometry + ":" + segment.Status + ":" + segment.GetRelationsCount();
        })) + (diameter == null ? "" : ((IDimension)diameter.GetDimension2(0)).SystemValue.ToString("R", CultureInfo.InvariantCulture));
    }

    private static string Coordinates(ISketchPoint point)
    {
        return string.Join(",", new[] { point.X, point.Y, point.Z }.Select(value => value.ToString("R", CultureInfo.InvariantCulture)));
    }

    private static string[] Selected(IModelDoc2 doc)
    {
        var selections = (ISelectionMgr)doc.SelectionManager;
        return Enumerable.Range(1, selections.GetSelectedObjectCount2(-1))
            .Select(index => Key((ISketchSegment)selections.GetSelectedObject6(index, -1)))
            .OrderBy(key => key).ToArray();
    }

    private static string Key(ISketchSegment segment)
    {
        var id = (int[])segment.GetID();
        return segment.GetType() + ":" + id[0] + ":" + id[1];
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}
