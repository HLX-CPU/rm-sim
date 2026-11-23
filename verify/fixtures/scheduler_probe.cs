// Deliberately NOT a finite-element solver. Exercises real OS process lifecycle.
using System;
using System.IO;
using System.Diagnostics;
using System.Threading;

public static class SchedulerProbe {
    public static int Main(string[] args) {
        string job = args.Length > 0 ? args[0] : "leaf";
        // Existence is the readiness signal used by parent/GUI tests. Publish
        // the PID atomically, so readers cannot observe an empty new file.
        File.WriteAllText(job + ".pid.tmp", Process.GetCurrentProcess().Id.ToString());
        File.Move(job + ".pid.tmp", job + ".pid");
        if (job.EndsWith(".leaf") || job == "unrelated") { Thread.Sleep(60000); return 0; }
        if (job == "spawn" || job == "orphan") {
            var info = new ProcessStartInfo(Process.GetCurrentProcess().MainModule.FileName, job + ".leaf");
            info.UseShellExecute = false; info.CreateNoWindow = true;
            var child = Process.Start(info);
            for (int n=0; n<100 && !File.Exists(job+".leaf.pid"); n++) Thread.Sleep(50);
            Console.WriteLine("child started " + child.Id);
            if (job == "orphan") return 0;
            Thread.Sleep(60000); return 0;
        }
        if (job == "flood") {
            Console.WriteLine("Job finished"); // marker is deliberately outside the final log tail
            byte[] block = new byte[65536]; for (int i=0;i<block.Length;i++) block[i]=(byte)'x';
            var output = Console.OpenStandardOutput();
            for (int i=0;i<512;i++) output.Write(block,0,block.Length);
            output.WriteByte(0xa1); output.Flush();
            var error = Console.OpenStandardError();
            for (int i=0;i<64;i++) error.Write(block,0,block.Length);
            error.Flush(); File.WriteAllText(job+".ready", "ready");
            for (int i=0;i<200 && !File.Exists(job+".release");i++) Thread.Sleep(50);
            return 0; // no DAT/FRD: must be classified as parse failure
        }
        if (job == "allocate") {
            try {
                byte[][] blocks = new byte[128][];
                for (int i=0;i<blocks.Length;i++) { blocks[i]=new byte[8*1024*1024]; Thread.Sleep(10); }
                Thread.Sleep(10000);
            } catch (OutOfMemoryException) { Console.Error.WriteLine("memory limit hit"); return 7; }
            return 0;
        }
        if (job == "stall_at_cap") {
            byte[][] blocks = new byte[512][];
            try {
                for (int i=0; i<blocks.Length; i++) blocks[i]=new byte[2*1024*1024];
            } catch (OutOfMemoryException) {
                Console.Error.WriteLine("allocation denied; waiting, not exiting");
            }
            File.WriteAllText(job+".ready", "allocation attempted");
            Thread.Sleep(60000);
            GC.KeepAlive(blocks);
            return 0;
        }
        if (job == "finished_with_error") {
            Console.WriteLine(" *ERROR in cascade: zero coefficient on the");
            Console.WriteLine("        dependent side of an equation");
            Console.WriteLine("        dependent node: 42        direction: 1");
            Console.Out.Flush();
            byte[] padding = new byte[65536];
            for (int i=0; i<padding.Length; i++) padding[i]=(byte)'x';
            var output = Console.OpenStandardOutput();
            for (int i=0; i<32; i++) output.Write(padding,0,padding.Length);
            output.WriteByte((byte)'\n'); output.Flush();
            Console.WriteLine("Job finished");
            return 0; // must fail before the deferred-parse path can mark solved
        }
        Console.Error.WriteLine("intentional process error"); return 23;
    }
}
