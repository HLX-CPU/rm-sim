using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

internal static class CircleDiameterVerification
{
    private const double FirstDiameterMm = 12.5;
    private const double SecondDiameterMm = 16.0;
    private const double GeometryTolerance = 1e-8;
    private const string LinkPrefix = "BatchCircleDiameter_";
    private static string interopDirectory;

    [STAThread]
    public static int Main(string[] args)
    {
        interopDirectory = args[1];
        AppDomain.CurrentDomain.AssemblyResolve += ResolveInterop;
        try
        {
            Run(args);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static Assembly ResolveInterop(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name;
        if (!name.StartsWith("SolidWorks.Interop.", StringComparison.Ordinal))
            return null;

        string path = Path.Combine(interopDirectory, name + ".dll");
        return File.Exists(path) ? Assembly.LoadFrom(path) : null;
    }

    private static void Run(string[] args)
    {
        var app = (ISldWorks)Marshal.GetActiveObject("SldWorks.Application");
        Console.WriteLine("SolidWorks revision: " + app.RevisionNumber());
        string runDirectory = args[2];
        if (args[0] == "PrepareToolbarTest")
        {
            PrepareToolbarTest(app, args[3], runDirectory);
            return;
        }
        RunSmoke(app, runDirectory);
        if (args[0] == "Smoke")
            return;

        string template = args[3];
        string source = ReadModule(args[4]);
        bool assemblySmoke = args[0] == "AssemblySmoke";
        var original = app.ActiveDoc as IModelDoc2;
        string originalTitle = original == null ? null : original.GetTitle();
        bool originalDirty = original != null && original.GetSaveFlag();
        bool originalDimPrompt = app.GetUserPreferenceToggle((int)swUserPreferenceToggle_e.swInputDimValOnCreate);
        int originalDocumentCount = app.GetDocumentCount();
        IModelDoc2 document = null;

        try
        {
            document = NewTestDocument(app, template, original);
            if (assemblySmoke)
            {
                VerifyAssemblySketch(app, document, runDirectory, source);
                return;
            }

            string earlyErrorSource = AddForcedError(source, args[5]);
            string lateErrorSource = AddForcedError(source, args[6]);

            var fixture = new Fixture(app, document);
            var activeSketch = (ISketch)document.SketchManager.ActiveSketch;
            Console.WriteLine("Active fixture state: IsSketchEditable={0}, GetAutomaticSolve={1}, GetConstrainedStatus={2}, IsOpenedReadOnly={3}",
                activeSketch.IsSketchEditable(), activeSketch.GetAutomaticSolve(), activeSketch.GetConstrainedStatus(), document.IsOpenedReadOnly());
            var before = fixture.Capture();
            Require(before.Dimensions.Count == 2, "Invalid fixture: expected two existing dimensions");
            Require(before.Segments[fixture.Keys["fixed"]].Status == (int)swConstrainedStatus_e.swFullyConstrained,
                "Invalid fixture: fixed circle is not fully constrained");
            Require(before.Segments[fixture.Keys["equal-a"]].Status == (int)swConstrainedStatus_e.swUnderConstrained,
                "Invalid fixture: equality-linked circle is not underconstrained");
            Console.WriteLine("Fixture: five eligible full circles, one pre-sized radius, one fixed circle, an arc and a line");

            VerifyRollback(app, fixture, before, runDirectory, "CircleForcedEarly", earlyErrorSource);
            VerifyRollback(app, fixture, before, runDirectory, "CircleForcedLate", lateErrorSource);

            string undoPath = Path.Combine(runDirectory, "CircleBatchUndo.result.txt");
            string applied = RunCore(app, document, runDirectory, "CircleBatchUndo", source, FirstDiameterMm, undoPath);
            Require(applied.StartsWith("OK ", StringComparison.Ordinal), "Batch before custom undo failed: " + applied);
            Require(File.Exists(undoPath), "The custom undo did not produce a result");
            string undoResult = File.ReadAllText(undoPath, Encoding.Default).Trim();
            Console.WriteLine("Custom undo: " + undoResult);
            RequireSnapshot(fixture, before, "Custom undo left CAD changes in the sketch");
            Require(undoResult == "UNDO_OK", "Custom undo did not report success");
            Console.WriteLine("PASS custom undo restored geometry, dimensions, relations and links");

            Apply(app, document, runDirectory, "CircleBatchTest", source, FirstDiameterMm);
            Snapshot sized = VerifyBatch(fixture, before, FirstDiameterMm);
            var firstLinkedDimension = sized.Dimensions.First(d => d.Link.StartsWith(LinkPrefix, StringComparison.Ordinal)).Dimension;
            Require(firstLinkedDimension.SetSystemValue3(SecondDiameterMm / 1000.0,
                (int)swSetValueInConfiguration_e.swSetValue_InThisConfiguration, null) == (int)swSetValueReturnStatus_e.swSetValue_Successful,
                "Editing the shared driving value failed");
            document.EditRebuild3();
            VerifyBatch(fixture, before, SecondDiameterMm);
            Console.WriteLine("PASS editing one driving dimension resized the other linked circles");

            string savePath = Path.Combine(runDirectory, "CircleDiameterFixture.SLDPRT");
            document.SketchManager.InsertSketch(true);
            int errors = 0;
            int warnings = 0;
            bool saved = document.Extension.SaveAs(savePath, 0, (int)swSaveAsOptions_e.swSaveAsOptions_Silent, null, ref errors, ref warnings);
            Require(saved && errors == 0 && File.Exists(savePath), "Fixture save failed: " + errors + "/" + warnings);
            app.CloseDoc(document.GetTitle());
            document = null;
            document = app.OpenDoc6(savePath, (int)swDocumentTypes_e.swDocPART, (int)swOpenDocOptions_e.swOpenDocOptions_Silent,
                "", ref errors, ref warnings);
            Require(document != null && errors == 0 && String.Equals(savePath, document.GetPathName(), StringComparison.OrdinalIgnoreCase),
                "Fixture reopen failed: " + errors + "/" + warnings);
            fixture.Document = document;
            VerifyBatch(fixture, before, SecondDiameterMm);
            Console.WriteLine("PASS native dimensions and link persisted after save/reopen");
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Fixture failure: " + error);
            throw;
        }
        finally
        {
            if (document != null && (original == null || app.IsSame(document, original) != 1))
                app.CloseDoc(document.GetTitle());
            if (original != null && app.IsSame(app.ActiveDoc, original) != 1)
            {
                int activationError = 0;
                app.ActivateDoc3(originalTitle, false, (int)swRebuildOnActivation_e.swDontRebuildActiveDoc, ref activationError);
            }
            if (original != null)
            {
                Require(app.IsSame(app.ActiveDoc, original) == 1, "Could not restore the original active document");
                Require(original.GetSaveFlag() == originalDirty, "Original document dirty status changed");
            }
            int endDocumentCount = app.GetDocumentCount();
            Require(endDocumentCount == originalDocumentCount,
                "Open document count changed from " + originalDocumentCount + " to " + endDocumentCount);
            Require(app.GetUserPreferenceToggle((int)swUserPreferenceToggle_e.swInputDimValOnCreate) == originalDimPrompt,
                "Dimension prompt preference was not restored");
        }
    }

    private static IModelDoc2 NewTestDocument(ISldWorks app, string template, IModelDoc2 original)
    {
        Console.WriteLine("Creating a disposable document from " + template);
        var document = (IModelDoc2)app.NewDocument(template, 0, 0, 0);
        Require(document != null, "Could not create a disposable document");
        Require(original == null || app.IsSame(document, original) != 1, "NewDocument returned the original document");
        Require(String.IsNullOrEmpty(document.GetPathName()), "NewDocument reused an existing file");
        Console.WriteLine("Disposable document created: " + document.GetTitle());
        RequireActive(app, document);
        return document;
    }

    private static ISketch BeginSketch(IModelDoc2 document, string name)
    {
        IFeature plane = document.FirstFeature() as IFeature;
        while (plane != null && plane.GetTypeName2() != "RefPlane")
            plane = plane.GetNextFeature() as IFeature;
        Require(plane != null && plane.Select2(false, 0), "Could not select the first standard plane");
        ISketchManager manager = document.SketchManager;
        manager.InsertSketch(true);
        ISketch sketch = manager.ActiveSketch;
        Require(sketch != null && !sketch.Is3D(), "Could not create a disposable 2D sketch");
        ((IFeature)sketch).Name = name;
        return sketch;
    }

    private static void VerifyAssemblySketch(ISldWorks app, IModelDoc2 document, string runDirectory, string source)
    {
        Require(document is IAssemblyDoc && document.GetConfigurationCount() == 1,
            "Assembly smoke requires a single-configuration assembly template");
        ISketch sketch = BeginSketch(document, Fixture.SketchName);
        ISketchManager manager = document.SketchManager;
        manager.AddToDB = true;
        try
        {
            Require(manager.CreateCircleByRadius(-0.02, 0.01, 0, 0.004) != null, "First assembly circle creation failed");
            Require(manager.CreateCircleByRadius(0.03, 0.025, 0, 0.007) != null, "Second assembly circle creation failed");
        }
        finally { manager.AddToDB = false; }
        IFeature feature = (IFeature)sketch;
        Snapshot before = Fixture.CaptureSketch(document, feature);
        Require(before.Segments.Count == 2 && before.Dimensions.Count == 0, "Unexpected assembly fixture geometry");
        Require(before.Segments.Values.All(segment => segment.Status == (int)swConstrainedStatus_e.swUnderConstrained),
            "Both assembly circle inputs should be underconstrained");
        string result = RunCore(app, document, runDirectory, "CircleAssemblySmoke", source, FirstDiameterMm);
        Require(result.StartsWith("OK 2|2|2|", StringComparison.Ordinal), "Assembly batch did not process both circles: " + result);
        Snapshot after = Fixture.CaptureSketch(document, feature);
        Require(after.Segments.Count == 2 && after.Dimensions.Count == 2, "Expected two assembly circles and two dimensions");
        foreach (var pair in before.Segments)
        {
            SegmentState segment = after.Segments[pair.Key];
            Require(SameNumbers(segment.Coordinates, pair.Value.Coordinates) && Near(segment.Diameter, FirstDiameterMm / 1000.0),
                "An assembly circle center moved or diameter differs");
        }
        string link = after.Dimensions[0].Link;
        Require(link.StartsWith(LinkPrefix, StringComparison.Ordinal) && after.Dimensions.All(dimension => dimension.Link == link &&
            dimension.Type == (int)swDimensionType_e.swDiameterDimension && dimension.State == (int)swDimensionDrivenState_e.swDimensionDriving),
            "Assembly driving diameters did not share one native link group");
        Console.WriteLine("PASS two linked diameter dimensions on an assembly-level 2D sketch");
    }

    private static void PrepareToolbarTest(ISldWorks app, string template, string runDirectory)
    {
        const string sketchName = "CircleDiameterToolbarSketch";
        var original = app.ActiveDoc as IModelDoc2;
        IModelDoc2 document = null;
        try
        {
            document = NewTestDocument(app, template, original);
            BeginSketch(document, sketchName);
            ISketchManager manager = document.SketchManager;
            manager.AddToDB = true;
            try
            {
                foreach (double[] values in new double[][] {
                    new double[] { -0.025, -0.017, 0.004 },
                    new double[] { 0.004, 0.020, 0.008 },
                    new double[] { 0.031, -0.008, 0.006 }
                })
                {
                    Require(manager.CreateCircleByRadius(values[0], values[1], 0, values[2]) != null, "Could not create toolbar test circle");
                }
            }
            finally { manager.AddToDB = false; }
            manager.InsertSketch(true);
            string savePath = Path.Combine(runDirectory, "CircleDiameterToolbarTest.SLDPRT");
            int errors = 0;
            int warnings = 0;
            bool saved = document.Extension.SaveAs(savePath, 0, (int)swSaveAsOptions_e.swSaveAsOptions_Silent, null, ref errors, ref warnings);
            Require(saved && errors == 0 && File.Exists(savePath), "Toolbar fixture save failed: " + errors + "/" + warnings);
            var sketchFeature = (IFeature)((IPartDoc)document).FeatureByName(sketchName);
            document.ClearSelection2(true);
            Require(sketchFeature.Select2(false, 0), "Could not select the toolbar test sketch");
            document.EditSketch();
            document.ClearSelection2(true);
            document.ViewZoomtofit2();
            Require(manager.ActiveSketch != null, "Toolbar test sketch is not open for editing");
            Console.WriteLine("Toolbar test part left active: " + document.GetTitle());
            Console.WriteLine("Path: " + savePath);
            Console.WriteLine("Original active document was: " + (original == null ? "<none>" : original.GetTitle()));
        }
        catch
        {
            if (document != null && (original == null || app.IsSame(document, original) != 1))
                app.CloseDoc(document.GetTitle());
            throw;
        }
    }

    private static void RequireActive(ISldWorks app, IModelDoc2 expected)
    {
        Require(app.IsSame(app.ActiveDoc, expected) == 1, "The disposable part is not active");
    }

    private static string ReadModule(string path)
    {
        string text = File.ReadAllText(path, Encoding.UTF8);
        text = Regex.Replace(text, @"^Attribute [^\r\n]*(?:\r?\n|$)", "", RegexOptions.Multiline);
        return Regex.Replace(text, @"^Public Sub (?:main|undoLastBatch)\(\)\r?\n[\s\S]*?^End Sub\s*", "", RegexOptions.Multiline | RegexOptions.IgnoreCase);
    }

    private static string AddForcedError(string source, string anchor)
    {
        Require(!String.IsNullOrWhiteSpace(anchor), "A rollback anchor is required");
        string[] lines = Regex.Split(source, @"\r?\n");
        Require(lines.Count(line => line.Trim() == anchor.Trim()) == 1, "The rollback anchor must match one whole line");
        return String.Join("\r\n", lines.Select(line => line.Trim() == anchor.Trim()
            ? line + "\r\n    Err.Raise vbObjectError + 1100, \"Verify\", \"forced rollback\""
            : line));
    }

    private static void RunSmoke(ISldWorks app, string runDirectory)
    {
        string resultPath = Path.Combine(runDirectory, "VbaProbe.txt");
        string source = "Option Explicit\r\n" +
            "Public Sub main()\r\n" +
            "    Dim app As SldWorks.SldWorks\r\n" +
            "    Set app = Application.SldWorks\r\n" +
            "    Open " + VbaString(resultPath) + " For Output As #1\r\n" +
            "    Print #1, app.RevisionNumber\r\n" +
            "    Close #1\r\n" +
            "End Sub\r\n";
        RunMacro(app, runDirectory, "VbaProbe", "main", source, resultPath);
        Require(File.ReadAllText(resultPath).Trim() == app.RevisionNumber(), "VBA smoke produced unexpected revision");
        Console.WriteLine("PASS VBA runtime and SOLIDWORKS type library");
    }

    private static void Apply(ISldWorks app, IModelDoc2 expectedDocument, string runDirectory, string name, string source, double millimeters)
    {
        string result = RunCore(app, expectedDocument, runDirectory, name, source, millimeters);
        Require(result.StartsWith("OK ", StringComparison.Ordinal), "Macro failed: " + result);
    }

    private static void VerifyRollback(ISldWorks app, Fixture fixture, Snapshot before, string runDirectory, string name, string source)
    {
        string failed = RunCore(app, fixture.Document, runDirectory, name, source, FirstDiameterMm);
        RequireSnapshot(fixture, before, name + " left CAD changes in the sketch");
        Require(failed.StartsWith("ERROR_ROLLED_BACK ", StringComparison.Ordinal) &&
            failed.EndsWith("forced rollback", StringComparison.Ordinal), "Expected the injected error and verified rollback, got: " + failed);
        Console.WriteLine("PASS " + name + " restored geometry, dimensions, relations and links");
    }

    private static void RequireSnapshot(Fixture fixture, Snapshot before, string message)
    {
        Snapshot actual = fixture.Capture();
        if (SnapshotsEqual(before, actual))
            return;
        ReportSnapshotDifferences(before, actual);
        throw new InvalidOperationException(message);
    }

    private static string RunCore(ISldWorks app, IModelDoc2 expectedDocument, string runDirectory, string name, string source, double millimeters,
        string undoResultPath = null)
    {
        RequireActive(app, expectedDocument);
        string resultPath = Path.Combine(runDirectory, name + ".txt");
        source += "\r\nPublic Sub main()\r\n" +
            "    Dim app As SldWorks.SldWorks\r\n" +
            "    Dim activeModel As SldWorks.ModelDoc2\r\n" +
            "    Dim activeSketch As SldWorks.Sketch\r\n" +
            "    Dim sketchFeature As SldWorks.Feature\r\n" +
            "    Dim result As String\r\n" +
            "    Set app = Application.SldWorks\r\n" +
            "    Set activeModel = app.ActiveDoc\r\n" +
            "    result = \"TEST_FIXTURE_NOT_ACTIVE\"\r\n" +
            "    If Not activeModel Is Nothing Then\r\n" +
            "        If activeModel.GetTitle = " + VbaString(expectedDocument.GetTitle()) + " And activeModel.GetPathName = \"\" Then\r\n" +
            "            Set activeSketch = activeModel.SketchManager.ActiveSketch\r\n" +
            "            If Not activeSketch Is Nothing Then\r\n" +
            "                Set sketchFeature = activeSketch\r\n" +
            "                If sketchFeature.Name = \"" + Fixture.SketchName + "\" Then result = ApplyBatchCircleDiameter(app, " + millimeters.ToString("R", CultureInfo.InvariantCulture) + ")\r\n" +
            "            End If\r\n" +
            "        End If\r\n" +
            "    End If\r\n" +
            "    Open " + VbaString(resultPath) + " For Output As #1\r\n" +
            "    Print #1, result\r\n" +
            "    Close #1\r\n";
        if (undoResultPath != null)
        {
            source += "    If Left$(result, 3) = \"OK \" Then\r\n" +
                "        result = UndoBatchCircleDiameter(app)\r\n" +
                "        Open " + VbaString(undoResultPath) + " For Output As #1\r\n" +
                "        Print #1, result\r\n" +
                "        Close #1\r\n" +
                "    End If\r\n";
        }
        source += "End Sub\r\n";
        RunMacro(app, runDirectory, name, "main", source, resultPath);
        string result = File.ReadAllText(resultPath, Encoding.Default).Trim();
        Console.WriteLine(name + ": " + result);
        return result;
    }

    private static string VbaString(string text)
    {
        var parts = new List<string>();
        var asciiRun = new StringBuilder();
        foreach (char ch in text)
        {
            if (ch >= 32 && ch <= 126)
            {
                asciiRun.Append(ch == '"' ? "\"\"" : ch.ToString());
                continue;
            }
            if (asciiRun.Length > 0)
            {
                parts.Add("\"" + asciiRun.ToString() + "\"");
                asciiRun.Clear();
            }
            parts.Add("ChrW(&H" + ((int)ch).ToString("X4", CultureInfo.InvariantCulture) + ")");
        }
        if (asciiRun.Length > 0)
            parts.Add("\"" + asciiRun.ToString() + "\"");
        return String.Join(" & ", parts);
    }

    private static void RunMacro(ISldWorks app, string runDirectory, string module, string procedure, string source, string resultPath)
    {
        const int unload = (int)swRunMacroOption_e.swRunMacroUnloadAfterRun;
        string path = Path.Combine(runDirectory, module + ".swb");
        var ascii = Encoding.GetEncoding("us-ascii", EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
        File.WriteAllText(path, Regex.Replace(source, "\r?\n", "\r\n"), ascii);
        int lastError = 0;
        foreach (string candidate in new[] { module, "Module1", module + "1", "" })
        {
            bool ran = app.RunMacro2(path, candidate, procedure, unload, out lastError);
            Console.WriteLine("{0}:{1}.{2}, returned {3}, error {4}", module, candidate, procedure, ran, lastError);
            if (ran && File.Exists(resultPath))
                return;
            if (File.Exists(resultPath))
                throw new InvalidOperationException("Macro wrote a result but reported an execution failure");
            if (ran || (lastError != 1 && lastError != 6 && lastError != 8 && lastError != 22 && lastError != 28))
                break;
        }
        throw new InvalidOperationException("RunMacro2 did not produce a result file, last error " + lastError);
    }

    private static Snapshot VerifyBatch(Fixture fixture, Snapshot original, double diameterMm)
    {
        Snapshot sized = fixture.Capture();
        Require(sized.Segments.Count == original.Segments.Count, "The macro changed the number of sketch segments");
        var targets = fixture.Targets.Select(role => fixture.Keys[role]).ToArray();
        foreach (var segment in original.Segments)
        {
            SegmentState current;
            Require(sized.Segments.TryGetValue(segment.Key, out current), "Sketch segment " + segment.Key + " is missing");
            Require(SameNumbers(current.Coordinates, segment.Value.Coordinates), "Center or endpoint moved for " + segment.Key);
            double expectedDiameter = targets.Contains(segment.Key) ? diameterMm / 1000.0 : segment.Value.Diameter;
            Require(Near(current.Diameter, expectedDiameter),
                String.Format(CultureInfo.InvariantCulture, "Segment {0}: expected {1:F4} mm, got {2:F4} mm", segment.Key, 1000 * expectedDiameter, 1000 * current.Diameter));
            Require(current.Status <= (int)swConstrainedStatus_e.swFullyConstrained, "Invalid constraint state on " + segment.Key);
        }
        var linked = sized.Dimensions.Where(d => d.Link.StartsWith(LinkPrefix, StringComparison.Ordinal)).ToArray();
        Require(linked.Length == 4 && linked.Select(d => d.Link).Distinct().Count() == 1,
            "Expected four driving diameters in one native shared-value group (the equal pair uses one dimension)");
        foreach (DimensionState dimension in linked)
        {
            Require(dimension.Type == (int)swDimensionType_e.swDiameterDimension && dimension.State == (int)swDimensionDrivenState_e.swDimensionDriving,
                "Expected a native driving diameter dimension for " + dimension.Name);
            Require(Near(dimension.Value, diameterMm / 1000.0), "Linked driving dimension value differs from the actual diameter");
        }
        Require(sized.Dimensions.Count == 5, "Unexpected extra/missing dimensions, expected original radius plus four diameters");
        Console.WriteLine("PASS {0:F2} mm verified in 5 circles, 4 linked diameter dimensions, centers and other geometry unchanged", diameterMm);
        return sized;
    }

    private static bool SnapshotsEqual(Snapshot a, Snapshot b)
    {
        if (a.Segments.Count != b.Segments.Count || a.Dimensions.Count != b.Dimensions.Count ||
            a.SketchStatus != b.SketchStatus || a.FeatureCount != b.FeatureCount)
            return false;
        foreach (var segment in a.Segments)
        {
            SegmentState value;
            if (!b.Segments.TryGetValue(segment.Key, out value) ||
                segment.Value.Status != value.Status || !segment.Value.RelationTypes.SequenceEqual(value.RelationTypes) ||
                !SameNumbers(segment.Value.Coordinates, value.Coordinates) ||
                !Near(segment.Value.Diameter, value.Diameter))
                return false;
        }
        return a.Dimensions.All(d =>
            b.Dimensions.Any(other => d.Name == other.Name && d.Link == other.Link && d.Type == other.Type &&
                d.State == other.State && Near(d.Value, other.Value)));
    }

    private static void ReportSnapshotDifferences(Snapshot before, Snapshot after)
    {
        Console.Error.WriteLine("Sketch constraint status {0} -> {1}, feature count {2} -> {3}",
            before.SketchStatus, after.SketchStatus, before.FeatureCount, after.FeatureCount);
        Console.Error.WriteLine("Original dimensions:");
        foreach (var dimension in before.Dimensions)
            Console.Error.WriteLine("{0} type={1} drivenState={2} value={3:R} link={4}", dimension.Name, dimension.Type, dimension.State, dimension.Value, dimension.Link);
        Console.Error.WriteLine("Dimensions after undo:");
        foreach (var dimension in after.Dimensions)
            Console.Error.WriteLine("{0} type={1} drivenState={2} value={3:R} link={4}", dimension.Name, dimension.Type, dimension.State, dimension.Value, dimension.Link);
        foreach (var segment in before.Segments)
        {
            SegmentState value;
            if (!after.Segments.TryGetValue(segment.Key, out value))
                Console.Error.WriteLine("Segment absent after undo: " + segment.Key);
            else if (segment.Value.Status != value.Status || !segment.Value.RelationTypes.SequenceEqual(value.RelationTypes) ||
                !SameNumbers(segment.Value.Coordinates, value.Coordinates) || !Near(segment.Value.Diameter, value.Diameter))
                Console.Error.WriteLine("{0}: old status={1}, actual status={2}, old diameter={3:R}, actual diameter={4:R}, same position={5}, relations {6} -> {7}",
                    segment.Key, segment.Value.Status, value.Status, segment.Value.Diameter, value.Diameter, SameNumbers(segment.Value.Coordinates, value.Coordinates),
                    String.Join(",", segment.Value.RelationTypes), String.Join(",", value.RelationTypes));
        }
    }

    private static bool SameNumbers(double[] a, double[] b)
    {
        return a.Length == b.Length && a.Zip(b, Near).All(pairMatches => pairMatches);
    }

    private static bool Near(double a, double b)
    {
        return Math.Abs(a - b) <= GeometryTolerance;
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }

    private sealed class Snapshot
    {
        public readonly Dictionary<string, SegmentState> Segments = new Dictionary<string, SegmentState>();
        public readonly List<DimensionState> Dimensions = new List<DimensionState>();
        public int SketchStatus;
        public int FeatureCount;
    }

    private sealed class SegmentState
    {
        public double[] Coordinates;
        public double Diameter;
        public int Status;
        public int[] RelationTypes;
    }

    private sealed class DimensionState
    {
        public string Name;
        public string Link;
        public int Type;
        public int State;
        public double Value;
        public IDimension Dimension;
    }

    private sealed class Fixture
    {
        public readonly string[] Targets = { "free-a", "free-b", "equal-a", "equal-b", "existing-diameter" };
        public readonly Dictionary<string, string> Keys = new Dictionary<string, string>();
        public IModelDoc2 Document;
        public const string SketchName = "CircleBatchRegression";

        public Fixture(ISldWorks app, IModelDoc2 document)
        {
            Document = document;
            ISketch sketch = BeginSketch(document, SketchName);
            ISketchManager manager = document.SketchManager;
            ISketchSegment equalA;
            ISketchSegment equalB;
            ISketchSegment sized;
            ISketchSegment radius;
            ISketchSegment fixedCircle;
            manager.AddToDB = true;
            try
            {
                Circle(manager, "free-a", -0.050, 0.036, 0.003);
                Circle(manager, "free-b", -0.020, 0.041, 0.004);
                equalA = Circle(manager, "equal-a", 0.018, 0.035, 0.006);
                equalB = Circle(manager, "equal-b", 0.062, 0.039, 0.006);
                sized = Circle(manager, "existing-diameter", -0.060, -0.010, 0.005);
                radius = Circle(manager, "existing-radius", -0.030, -0.013, 0.007);
                fixedCircle = Circle(manager, "fixed", 0.014, -0.012, 0.008);
                Keys.Add("arc", Key(manager.CreateArc(0.052, -0.015, 0, 0.060, -0.015, 0, 0.052, -0.007, 0, 1)));
                Keys.Add("line", Key(manager.CreateLine(-0.07, -0.05, 0, 0.07, -0.05, 0)));
            }
            finally { manager.AddToDB = false; }

            document.ClearSelection2(true);
            Require(equalA.Select4(false, null) && equalB.Select4(true, null), "Could not select the equal pair");
            document.SketchAddConstraints("sgSAMELENGTH");
            Require(HasRelation(equalA, swConstraintType_e.swConstraintType_SAMELENGTH), "Could not create the equality relation");

            document.ClearSelection2(true);
            Require(fixedCircle.Select4(false, null), "Could not select the fixed control circle");
            document.SketchAddConstraints("sgFIXED");
            Require(HasRelation(fixedCircle, swConstraintType_e.swConstraintType_FIXED), "Could not fix the control circle");

            const int preference = (int)swUserPreferenceToggle_e.swInputDimValOnCreate;
            const int scalePreference = (int)swUserPreferenceToggle_e.swScaleSketchOnFirstDimension;
            bool oldPreference = app.GetUserPreferenceToggle(preference);
            bool oldScale = app.GetUserPreferenceToggle(scalePreference);
            try
            {
                if (oldPreference)
                {
                    app.SetUserPreferenceToggle(preference, false);
                    Require(!app.GetUserPreferenceToggle(preference), "Could not suppress fixture dimension prompts");
                }
                if (oldScale)
                    app.SetUserPreferenceToggle(scalePreference, false);
                AddDimension(app, sketch, sized, (int)swDimensionType_e.swDiameterDimension);
                AddDimension(app, sketch, radius, (int)swDimensionType_e.swRadialDimension);
            }
            finally
            {
                if (oldPreference)
                    app.SetUserPreferenceToggle(preference, true);
                if (oldScale)
                    app.SetUserPreferenceToggle(scalePreference, true);
                document.ClearSelection2(true);
            }
            document.ViewZoomtofit2();
        }

        private ISketchSegment Circle(ISketchManager manager, string role, double x, double y, double radius)
        {
            ISketchSegment segment = manager.CreateCircleByRadius(x, y, 0, radius);
            Keys.Add(role, Key(segment));
            return segment;
        }

        private static bool HasRelation(ISketchSegment segment, swConstraintType_e kind)
        {
            var relations = segment.GetRelations() as object[];
            return relations != null && relations.Cast<ISketchRelation>().Any(relation => relation.GetRelationType() == (int)kind);
        }

        private void AddDimension(ISldWorks app, ISketch sketch, ISketchSegment segment, int type)
        {
            Document.ClearSelection2(true);
            Require(segment.Select4(false, null), "Could not select the fixture circle");
            ISketchPoint center = (ISketchPoint)((ISketchArc)segment).GetCenterPoint2();
            IMathUtility utility = (IMathUtility)app.GetMathUtility();
            IMathPoint point = (IMathPoint)utility.CreatePoint(new double[] { center.X + 0.008, center.Y + 0.012, 0 });
            var location = (double[])((IMathPoint)point.MultiplyTransform(sketch.ModelToSketchTransform.IInverse())).ArrayData;
            int error = 0;
            var display = Document.Extension.AddSpecificDimension(location[0], location[1], location[2], type, ref error) as IDisplayDimension;
            Require(error == 0 && display != null && ((IDimension)display.GetDimension2(0)).DrivenState == (int)swDimensionDrivenState_e.swDimensionDriving,
                "Could not create the fixture driving dimension, status " + error);
        }

        public Snapshot Capture()
        {
            var feature = ((IPartDoc)Document).FeatureByName(SketchName) as IFeature;
            Require(feature != null, "Disposable sketch not found");
            return CaptureSketch(Document, feature);
        }

        public static Snapshot CaptureSketch(IModelDoc2 document, IFeature feature)
        {
            var sketch = (ISketch)feature.GetSpecificFeature2();
            var snapshot = new Snapshot { SketchStatus = sketch.GetConstrainedStatus() };
            for (var candidate = document.FirstFeature() as IFeature; candidate != null; candidate = candidate.GetNextFeature() as IFeature)
                snapshot.FeatureCount++;
            foreach (ISketchSegment segment in (object[])sketch.GetSketchSegments())
            {
                var relations = segment.GetRelations() as object[];
                var state = new SegmentState {
                    Status = segment.Status,
                    RelationTypes = relations == null ? new int[0] : relations.Cast<ISketchRelation>().Select(relation => relation.GetRelationType())
                        .OrderBy(kind => kind).ToArray()
                };
                var arc = segment as ISketchArc;
                if (arc != null)
                {
                    state.Coordinates = Coordinates((ISketchPoint)arc.GetCenterPoint2());
                    state.Diameter = 2.0 * arc.GetRadius();
                }
                else
                {
                    var line = (ISketchLine)segment;
                    state.Coordinates = Coordinates((ISketchPoint)line.GetStartPoint2())
                        .Concat(Coordinates((ISketchPoint)line.GetEndPoint2())).ToArray();
                }
                snapshot.Segments.Add(Key(segment), state);
            }

            var display = feature.GetFirstDisplayDimension() as IDisplayDimension;
            while (display != null)
            {
                var dimension = (IDimension)display.GetDimension2(0);
                snapshot.Dimensions.Add(new DimensionState {
                    Name = display.GetNameForSelection(),
                    Link = display.IsLinked ? display.GetLinkedText() ?? "" : "",
                    Type = display.Type2,
                    State = dimension.DrivenState,
                    Value = ((double[])dimension.GetSystemValue3((int)swInConfigurationOpts_e.swThisConfiguration, null))[0],
                    Dimension = dimension
                });
                display = feature.GetNextDisplayDimension(display) as IDisplayDimension;
            }
            return snapshot;
        }

        private static string Key(ISketchSegment segment)
        {
            Require(segment != null, "Sketch entity creation failed");
            var id = (int[])segment.GetID();
            return segment.GetType() + ":" + id[0] + ":" + id[1];
        }

        private static double[] Coordinates(ISketchPoint point)
        {
            return new double[] { point.X, point.Y, point.Z };
        }
    }
}
