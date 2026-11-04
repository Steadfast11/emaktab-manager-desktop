using System;
using EMaktabWebsite;

// Developer-only harness: runs the Portable restore/merge/save logic outside the Windows launcher (used on macOS with mono).
static class PortableHarness {
    static int Main(string[] args) {
        try {
            var store=new Portable(args[1],args[2],args[3]);
            if(args[0]=="prepare") store.Prepare(delegate(string text) { Console.WriteLine(text); });
            else if(args[0]=="save") store.Save(args[4]);
            else return 2;
            return 0;
        } catch(Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
