param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$interop = 'D:\sw2026\sw\SOLIDWORKS\api\redist'
Add-Type -Path (Join-Path $interop 'SolidWorks.Interop.sldworks.dll')
Add-Type -Path (Join-Path $interop 'SolidWorks.Interop.swconst.dll')
$code = @'
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

public class NativePartExport {
 public string Source, Configuration, Output, HashBefore, HashAfter, Material, MaterialDatabase;
 public bool Success; public int Errors, Warnings; public double VolumeM3;
}
public class NativeInstance {
 public string Name, Source, Configuration; public bool Suppressed, Hidden, Resolved;
 public double[] Transform;
}
public class NativeExportResult {
 public string Source, HashBefore, HashAfter, Configuration, AssemblyStep;
 public bool Success, PreferencesRestored, StartedApplication, RebuildSucceeded;
 public int OpenErrors, OpenWarnings, ExportErrors, ExportWarnings;
 public double VolumeM3;
 public List<NativePartExport> Parts=new List<NativePartExport>();
 public List<NativeInstance> Instances=new List<NativeInstance>();
 public List<string> Problems=new List<string>();
}
public static class NativeAssemblyExporter {
 [DllImport("ole32.dll",CharSet=CharSet.Unicode)] static extern int CLSIDFromProgID(string p,out Guid g);
 [DllImport("oleaut32.dll",PreserveSig=false)] static extern void GetActiveObject(ref Guid g,IntPtr p,[MarshalAs(UnmanagedType.IUnknown)]out object o);
 static string Hash(string p) {
  using(var s=new FileStream(p,FileMode.Open,FileAccess.Read,FileShare.ReadWrite))
  using(var h=SHA256.Create()) return BitConverter.ToString(h.ComputeHash(s)).Replace("-","");
 }
 static double Volume(IModelDoc2 m) { return ((IMassProperty)m.Extension.CreateMassProperty()).Volume; }
 static string Safe(string s) { foreach(char c in Path.GetInvalidFileNameChars()) s=s.Replace(c,'_'); return s; }
 static void Activate(ISldWorks a,IModelDoc2 d) {
  int e=0; if(a.ActivateDoc3(d.GetTitle(),false,0,ref e)==null) throw new Exception("Activate failed: "+e);
 }
 static bool Save(ISldWorks a,IModelDoc2 d,string p,ref int e,ref int w) {
  if(File.Exists(p)) throw new IOException("Output exists: "+p);
  Activate(a,d); d.ClearSelection2(true);
  return d.Extension.SaveAs(p,0,3,null,ref e,ref w) && e==0 && File.Exists(p) && new FileInfo(p).Length>0;
 }
 public static NativeExportResult Run(string source,string output) {
  var r=new NativeExportResult(); r.Source=source; r.HashBefore=Hash(source);
  ISldWorks app=null; IModelDoc2 model=null, previous=null; bool opened=false;
  var visibility=new Dictionary<IComponent2,int>();
  var hashes=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
  var preferences=new Dictionary<int,int>();
  try {
   // QProcess opens its two redirected log files before launching this script.
   if(Directory.Exists(output)) foreach(string entry in Directory.GetFileSystemEntries(output)) {
    string name=Path.GetFileName(entry);
    if(name!="export.stdout.log" && name!="export.stderr.log") throw new IOException("Output directory must be empty (except process logs)");
   }
   Directory.CreateDirectory(output); Directory.CreateDirectory(Path.Combine(output,"parts"));
   Guid id; CLSIDFromProgID("SldWorks.Application",out id); object obj;
   try { GetActiveObject(ref id,IntPtr.Zero,out obj); }
   catch(COMException) { obj=Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application")); r.StartedApplication=true; }
   app=(ISldWorks)obj; if(r.StartedApplication) app.Visible=false;
   previous=(IModelDoc2)app.ActiveDoc;
   model=(IModelDoc2)app.GetOpenDocumentByName(source);
   if(model!=null && model.GetSaveFlag()) throw new Exception("Source has unsaved changes; export refused");
   if(model==null) { model=(IModelDoc2)app.OpenDoc6(source,2,3,"",ref r.OpenErrors,ref r.OpenWarnings); opened=true; }
   if(model==null || r.OpenErrors!=0) throw new Exception("OpenDoc6 failed: "+r.OpenErrors);
   r.Configuration=model.ConfigurationManager.ActiveConfiguration.Name;
   IAssemblyDoc asm=(IAssemblyDoc)model; asm.ResolveAllLightWeightComponents(false);
   object[] comps=(object[])asm.GetComponents(false);
   if(comps==null) throw new Exception("No components");
   var parts=new Dictionary<string,Tuple<IModelDoc2,string>>(StringComparer.OrdinalIgnoreCase);
   foreach(object o in comps) {
    var c=(IComponent2)o;
    var i=new NativeInstance(); i.Name=c.Name2; i.Source=c.GetPathName(); i.Configuration=c.ReferencedConfiguration;
    i.Suppressed=c.IsSuppressed(); i.Hidden=c.Visible==0;
    IModelDoc2 d=(IModelDoc2)c.GetModelDoc2(); i.Resolved=d!=null;
    if(c.Transform2!=null) i.Transform=(double[])c.Transform2.ArrayData;
    r.Instances.Add(i);
    if(i.Suppressed) continue;
    if(d==null) throw new Exception("Unresolved component: "+i.Name);
    if(d.GetSaveFlag()) throw new Exception("Component has unsaved changes: "+i.Source);
    if(File.Exists(i.Source) && !hashes.ContainsKey(i.Source)) hashes[i.Source]=Hash(i.Source);
    if(d.GetType()==1) {
     string key=i.Source+"|"+i.Configuration;
     if(!parts.ContainsKey(key)) parts.Add(key,Tuple.Create(d,i.Configuration));
    }
    visibility[c]=c.Visible; if(i.Hidden) c.Visible=1;
   }
   foreach(int pref in new[]{(int)swUserPreferenceIntegerValue_e.swStepAP,(int)swUserPreferenceIntegerValue_e.swStepExportPreference})
    preferences[pref]=app.GetUserPreferenceIntegerValue(pref);
   app.SetUserPreferenceIntegerValue((int)swUserPreferenceIntegerValue_e.swStepAP,214);
   app.SetUserPreferenceIntegerValue((int)swUserPreferenceIntegerValue_e.swStepExportPreference,0);
   // Rebuild in memory so a NeedsRegen warning cannot leave stale mate positions
   // or stale part geometry in the analysis export. Never save native documents.
   r.RebuildSucceeded=model.ForceRebuild3(false);
   if(!r.RebuildSucceeded) throw new Exception("In-memory assembly rebuild failed");
   for(int k=0;k<comps.Length;k++) {
    var component=(IComponent2)comps[k];
    if(component.Transform2!=null) r.Instances[k].Transform=(double[])component.Transform2.ArrayData;
   }
   r.VolumeM3=Volume(model); r.AssemblyStep=Path.Combine(output,"assembly.step");
   if(!Save(app,model,r.AssemblyStep,ref r.ExportErrors,ref r.ExportWarnings)) throw new Exception("Assembly export failed: "+r.ExportErrors);
   int n=0;
   foreach(var pair in parts) {
    var d=pair.Value.Item1; string config=pair.Value.Item2, original=d.ConfigurationManager.ActiveConfiguration.Name;
    var p=new NativePartExport(); p.Source=d.GetPathName(); p.Configuration=config;
    p.HashBefore=hashes[p.Source]; p.Output=Path.Combine(output,"parts",(++n).ToString("D3")+"_"+Safe(Path.GetFileNameWithoutExtension(p.Source))+".step");
    r.Parts.Add(p);
    try {
     if(original!=config && !d.ShowConfiguration2(config)) throw new Exception("Configuration unavailable: "+config);
     string db=""; p.Material=((IPartDoc)d).GetMaterialPropertyName2(config,out db); p.MaterialDatabase=db;
     p.VolumeM3=Volume(d); p.Success=Save(app,d,p.Output,ref p.Errors,ref p.Warnings);
     if(!p.Success) throw new Exception("Part export failed: "+p.Source);
    } finally { if(original!=config) d.ShowConfiguration2(original); p.HashAfter=Hash(p.Source); }
   }
   foreach(var h in hashes) if(Hash(h.Key)!=h.Value) throw new Exception("Source changed: "+h.Key);
   r.Success=true;
  } catch(Exception e) { r.Problems.Add(e.ToString()); }
  finally {
   try {
    foreach(var v in visibility) v.Key.Visible=v.Value;
    r.PreferencesRestored=true;
    if(app!=null) foreach(var p in preferences) { app.SetUserPreferenceIntegerValue(p.Key,p.Value); if(app.GetUserPreferenceIntegerValue(p.Key)!=p.Value) r.PreferencesRestored=false; }
    if(app!=null && opened && model!=null) app.CloseDoc(model.GetTitle());
    if(app!=null && previous!=null && !r.StartedApplication) Activate(app,previous);
   } catch(Exception e) { r.Problems.Add("Cleanup: "+e); r.Success=false; }
   if(app!=null && r.StartedApplication) { try { app.ExitApp(); } catch(Exception e) { r.Problems.Add("Exit: "+e); r.Success=false; } }
   r.HashAfter=Hash(source); if(r.HashBefore!=r.HashAfter || !r.PreferencesRestored) r.Success=false;
  }
  return r;
 }
}
'@
Add-Type -ReferencedAssemblies @((Join-Path $interop 'SolidWorks.Interop.sldworks.dll'),(Join-Path $interop 'SolidWorks.Interop.swconst.dll')) -TypeDefinition $code
$result = [NativeAssemblyExporter]::Run([IO.Path]::GetFullPath($Source),[IO.Path]::GetFullPath($OutputDirectory))
$json = $result | ConvertTo-Json -Depth 12
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'native_export.json'),$json,[Text.UTF8Encoding]::new($false))
Write-Output $json
if (-not $result.Success) { exit 1 }
