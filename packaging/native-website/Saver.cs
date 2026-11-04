using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

[assembly: AssemblyTitle("eMaktab data saver")]
[assembly: AssemblyVersion("1.2.0.0")]

// Waits for the launcher to exit, then rewrites the data trailer at the end of eMaktab.exe in place.
// Layout: [program bytes][data blob (zip)][footer: "EMKDATA1" + int64 blob length + SHA-256 of blob]
namespace EMaktabSaver {
    static class Program {
        const string Magic = "EMKDATA1";
        const int FooterSize = 48;

        static byte[] Sha(byte[] bytes) { using(var digest=SHA256.Create()) return digest.ComputeHash(bytes); }

        static bool ReadFully(Stream stream, byte[] buffer) {
            int read=0; while(read<buffer.Length) { int n=stream.Read(buffer,read,buffer.Length-read); if(n<=0) return false; read+=n; }
            return true;
        }

        static long ProgramEnd(FileStream file, out byte[] oldTrailer) {
            oldTrailer=new byte[0];
            long length=file.Length;
            if(length<FooterSize) return length;
            var footer=new byte[FooterSize]; file.Seek(length-FooterSize,SeekOrigin.Begin);
            if(!ReadFully(file,footer) || Encoding.ASCII.GetString(footer,0,8)!=Magic) return length;
            long blob=BitConverter.ToInt64(footer,8);
            if(blob<0 || blob>length-FooterSize) return length;
            long end=length-FooterSize-blob;
            var trailer=new byte[length-end]; file.Seek(end,SeekOrigin.Begin);
            if(!ReadFully(file,trailer)) return length;
            oldTrailer=trailer;
            return end;
        }

        static bool Verify(string target, byte[] expected) {
            using(var file=new FileStream(target,FileMode.Open,FileAccess.Read,FileShare.Read)) {
                if(file.Length<FooterSize) return false;
                var footer=new byte[FooterSize]; file.Seek(file.Length-FooterSize,SeekOrigin.Begin);
                if(!ReadFully(file,footer) || Encoding.ASCII.GetString(footer,0,8)!=Magic) return false;
                long size=BitConverter.ToInt64(footer,8); if(size<0 || size>file.Length-FooterSize) return false;
                var blob=new byte[size]; file.Seek(file.Length-FooterSize-size,SeekOrigin.Begin);
                if(!ReadFully(file,blob)) return false;
                var digest=Sha(blob);
                for(int i=0;i<32;i++) if(digest[i]!=expected[i] || footer[16+i]!=expected[i]) return false;
                return true;
            }
        }

        static bool Write(string target, byte[] blob, byte[] digest) {
            using(var file=new FileStream(target,FileMode.Open,FileAccess.ReadWrite,FileShare.None)) {
                byte[] old; long end=ProgramEnd(file,out old);
                try {
                    file.SetLength(end); file.Seek(end,SeekOrigin.Begin);
                    file.Write(blob,0,blob.Length);
                    file.Write(Encoding.ASCII.GetBytes(Magic),0,8);
                    file.Write(BitConverter.GetBytes((long)blob.Length),0,8);
                    file.Write(digest,0,32);
                    file.Flush(true);
                } catch {
                    try { file.SetLength(end); file.Seek(end,SeekOrigin.Begin); file.Write(old,0,old.Length); file.Flush(true); } catch { }
                    throw;
                }
            }
            return Verify(target,digest);
        }

        static int Main(string[] args) {
            try {
                if(args.Length<3) return 2;
                string target=args[0], blobPath=args[1];
                int pid; if(!int.TryParse(args[2],out pid)) return 2;
                try { using(var parent=Process.GetProcessById(pid)) parent.WaitForExit(120000); } catch(ArgumentException) { }
                if(!File.Exists(blobPath) || !File.Exists(target)) return 3;
                byte[] blob=File.ReadAllBytes(blobPath); var digest=Sha(blob);
                for(int attempt=0;attempt<90;attempt++) {
                    try {
                        if(Write(target,blob,digest)) { try { File.Delete(blobPath); } catch { } return 0; }
                        return 4;
                    } catch(IOException) { Thread.Sleep(1000); }
                    catch(UnauthorizedAccessException) { Thread.Sleep(1000); }
                }
                return 5;
            } catch { return 1; }
        }
    }
}
