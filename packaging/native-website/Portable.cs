using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

// Portable account storage: the accounts travel inside eMaktab.exe itself.
// Layout of the exe: [program][data blob (zip)][footer: "EMKDATA1" + int64 blob length + SHA-256 of blob]
// The blob holds manager.sqlite3, vault.key and meta.json. Saver.exe rewrites it after the launcher exits.
namespace EMaktabWebsite {
    sealed class TrailerMeta { public string SavedAt, Digest; public int Accounts; }
    sealed class Trailer { public byte[] Blob; public TrailerMeta Meta; }
    sealed class DataState { public string Digest=""; public int Accounts; }

    static class Fernet {
        public static byte[] FromUrlBase64(string text) {
            string s=text.Trim().Replace('-','+').Replace('_','/');
            switch(s.Length%4) { case 2: s+="=="; break; case 3: s+="="; break; }
            return Convert.FromBase64String(s);
        }
        public static string ToUrlBase64(byte[] bytes) { return Convert.ToBase64String(bytes).Replace('+','-').Replace('/','_'); }
        static byte[] Key(byte[] keyFile) {
            byte[] key=FromUrlBase64(Encoding.ASCII.GetString(keyFile));
            if(key.Length!=32) throw new InvalidDataException("Bad vault key");
            return key;
        }
        static byte[] Part(byte[] source,int start,int length) { var part=new byte[length]; Buffer.BlockCopy(source,start,part,0,length); return part; }
        static byte[] Sign(byte[] signingKey,byte[] data,int length) { using(var hmac=new HMACSHA256(signingKey)) return hmac.ComputeHash(data,0,length); }
        public static byte[] Decrypt(byte[] keyFile,byte[] token) {
            byte[] key=Key(keyFile), signingKey=Part(key,0,16), encryptionKey=Part(key,16,16);
            byte[] data=FromUrlBase64(Encoding.ASCII.GetString(token));
            if(data.Length<1+8+16+16+32 || data[0]!=0x80) throw new InvalidDataException("Bad token");
            byte[] expected=Sign(signingKey,data,data.Length-32); int difference=0;
            for(int i=0;i<32;i++) difference|=expected[i]^data[data.Length-32+i];
            if(difference!=0) throw new InvalidDataException("Token signature mismatch");
            using(var aes=Aes.Create()) {
                aes.Key=encryptionKey; aes.IV=Part(data,9,16); aes.Mode=CipherMode.CBC; aes.Padding=PaddingMode.PKCS7;
                int cipherLength=data.Length-32-25;
                using(var decryptor=aes.CreateDecryptor()) return decryptor.TransformFinalBlock(data,25,cipherLength);
            }
        }
        public static byte[] Encrypt(byte[] keyFile,byte[] plain) {
            byte[] key=Key(keyFile), signingKey=Part(key,0,16), encryptionKey=Part(key,16,16);
            var iv=new byte[16]; using(var random=RandomNumberGenerator.Create()) random.GetBytes(iv);
            byte[] cipher;
            using(var aes=Aes.Create()) {
                aes.Key=encryptionKey; aes.IV=iv; aes.Mode=CipherMode.CBC; aes.Padding=PaddingMode.PKCS7;
                using(var encryptor=aes.CreateEncryptor()) cipher=encryptor.TransformFinalBlock(plain,0,plain.Length);
            }
            long seconds=(long)(DateTime.UtcNow-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalSeconds;
            var data=new byte[1+8+16+cipher.Length+32]; data[0]=0x80;
            for(int i=0;i<8;i++) data[1+i]=(byte)((seconds>>(8*(7-i)))&0xff);
            Buffer.BlockCopy(iv,0,data,9,16); Buffer.BlockCopy(cipher,0,data,25,cipher.Length);
            byte[] signature=Sign(signingKey,data,data.Length-32); Buffer.BlockCopy(signature,0,data,data.Length-32,32);
            return Encoding.ASCII.GetBytes(ToUrlBase64(data));
        }
    }

    sealed class Sqlite {
        readonly string exe; public int LastExit; public string LastError="";
        public Sqlite(string path) { exe=path; }
        public string Run(string database,string workDir,string script) {
            var info=new ProcessStartInfo(exe,"-batch -json \""+database+"\"") {UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,WorkingDirectory=workDir};
            using(var process=Process.Start(info)) {
                var error=new StringBuilder();
                process.ErrorDataReceived+=delegate(object sender,DataReceivedEventArgs e) { if(e.Data!=null) error.AppendLine(e.Data); };
                process.BeginErrorReadLine();
                byte[] input=new UTF8Encoding(false).GetBytes(".timeout 10000\n"+script+"\n");
                process.StandardInput.BaseStream.Write(input,0,input.Length); process.StandardInput.BaseStream.Flush(); process.StandardInput.Close();
                string output; using(var reader=new StreamReader(process.StandardOutput.BaseStream,new UTF8Encoding(false))) output=reader.ReadToEnd();
                if(!process.WaitForExit(60000)) { try { process.Kill(); } catch { } throw new IOException("sqlite3 timed out"); }
                process.WaitForExit(); LastExit=process.ExitCode; LastError=error.ToString();
                return output;
            }
        }
        public List<Dictionary<string,object>> Query(string database,string workDir,string select) {
            string output=Run(database,workDir,select+";");
            if(LastExit!=0) throw new IOException("sqlite3 query failed");
            if(output.Trim().Length==0) return new List<Dictionary<string,object>>();
            return new JavaScriptSerializer {MaxJsonLength=int.MaxValue}.Deserialize<List<Dictionary<string,object>>>(output);
        }
    }

    sealed class Portable {
        const string Magic="EMKDATA1"; const int FooterSize=48;
        static readonly JavaScriptSerializer Json=new JavaScriptSerializer {MaxJsonLength=int.MaxValue};
        readonly string exe,data,work,db,key; readonly Sqlite sql;
        public Portable(string exePath,string dataDir,string sqliteExe) {
            exe=exePath; data=dataDir; work=Path.Combine(dataDir,"portable-work");
            db=Path.Combine(dataDir,"manager.sqlite3"); key=Path.Combine(dataDir,"vault.key"); sql=new Sqlite(sqliteExe);
        }

        public void Log(string message) {
            try { Directory.CreateDirectory(data); File.AppendAllText(Path.Combine(data,"portable.log"),DateTime.UtcNow.ToString("o")+" "+message+"\r\n"); } catch { }
        }
        static string Hex(byte[] bytes) { var text=new StringBuilder(bytes.Length*2); foreach(byte b in bytes) text.Append(b.ToString("x2")); return text.ToString(); }
        static byte[] FromHex(string text) { var bytes=new byte[text.Length/2]; for(int i=0;i<bytes.Length;i++) bytes[i]=Convert.ToByte(text.Substring(i*2,2),16); return bytes; }
        static string Quote(string value) { return "'"+value.Replace("'","''")+"'"; }
        static string NewId() { var bytes=new byte[8]; using(var random=RandomNumberGenerator.Create()) random.GetBytes(bytes); return Hex(bytes); }
        static string Text(object value) { return Convert.ToString(value); }
        static bool ReadFully(Stream stream,byte[] buffer) { int read=0; while(read<buffer.Length) { int n=stream.Read(buffer,read,buffer.Length-read); if(n<=0) return false; read+=n; } return true; }
        static byte[] Entry(ZipArchive zip,string name) {
            var entry=zip.GetEntry(name); if(entry==null) throw new InvalidDataException("Missing "+name);
            using(var input=entry.Open()) using(var output=new MemoryStream()) { input.CopyTo(output); return output.ToArray(); }
        }
        static void Add(ZipArchive zip,string name,byte[] bytes) { var entry=zip.CreateEntry(name,CompressionLevel.Optimal); using(var output=entry.Open()) output.Write(bytes,0,bytes.Length); }

        string SyncPath { get { return Path.Combine(data,"portable.sync"); } }
        string ReadSync() { try { return File.Exists(SyncPath) ? File.ReadAllText(SyncPath).Trim() : ""; } catch { return ""; } }
        void WriteSync(string digest) { try { File.WriteAllText(SyncPath,digest); } catch { } }

        // --- trailer inside the exe ---
        static Trailer Parse(FileStream file) {
            if(file.Length<FooterSize) return null;
            var footer=new byte[FooterSize]; file.Seek(file.Length-FooterSize,SeekOrigin.Begin);
            if(!ReadFully(file,footer) || Encoding.ASCII.GetString(footer,0,8)!=Magic) return null;
            long size=BitConverter.ToInt64(footer,8); if(size<=0 || size>file.Length-FooterSize || size>int.MaxValue) return null;
            var blob=new byte[size]; file.Seek(file.Length-FooterSize-size,SeekOrigin.Begin);
            if(!ReadFully(file,blob)) return null;
            byte[] digest; using(var sha=SHA256.Create()) digest=sha.ComputeHash(blob);
            for(int i=0;i<32;i++) if(digest[i]!=footer[16+i]) return null;
            try {
                using(var zip=new ZipArchive(new MemoryStream(blob),ZipArchiveMode.Read)) {
                    var meta=Json.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(Entry(zip,"meta.json")));
                    return new Trailer {Blob=blob,Meta=new TrailerMeta {SavedAt=Text(meta["savedAt"]),Digest=Text(meta["digest"]),Accounts=Convert.ToInt32(meta["accounts"])}};
                }
            } catch(Exception) { return null; }
        }
        public Trailer ReadTrailer() {
            if(!File.Exists(exe)) return null;
            for(int attempt=0;attempt<20;attempt++) {
                try { using(var file=new FileStream(exe,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)) return Parse(file); }
                catch(IOException) { Thread.Sleep(500); }
            }
            return null;
        }

        // --- account state ---
        DataState Inspect(string database) {
            var state=new DataState();
            if(!File.Exists(database)) return state;
            try {
                var accounts=sql.Query(database,work,"SELECT id,label,hex(secret) AS s,group_id FROM accounts ORDER BY id");
                var groups=sql.Query(database,work,"SELECT id,name FROM account_groups ORDER BY id");
                var text=new StringBuilder();
                foreach(var row in accounts) text.Append("A|").Append(Text(row["id"])).Append('|').Append(Text(row["label"])).Append('|').Append(Text(row["s"])).Append('|').Append(Text(row["group_id"])).Append('\n');
                foreach(var row in groups) text.Append("G|").Append(Text(row["id"])).Append('|').Append(Text(row["name"])).Append('\n');
                using(var sha=SHA256.Create()) state.Digest=Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(text.ToString())));
                state.Accounts=accounts.Count;
            } catch(Exception) { state.Digest=""; state.Accounts=0; }
            return state;
        }

        void BackupLive(string reason) {
            try {
                string root=Path.Combine(data,"portable-backups"); string folder=Path.Combine(root,DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+"-"+reason);
                Directory.CreateDirectory(folder);
                if(File.Exists(db)) File.Copy(db,Path.Combine(folder,"manager.sqlite3"),true);
                if(File.Exists(key)) File.Copy(key,Path.Combine(folder,"vault.key"),true);
                var folders=new List<string>(Directory.GetDirectories(root)); folders.Sort();
                for(int i=0;i<folders.Count-8;i++) try { Directory.Delete(folders[i],true); } catch { }
            } catch(Exception e) { Log("backup failed: "+e.GetType().Name); }
        }

        static void WriteAtomic(string path,byte[] bytes) {
            string temp=path+".tmp"; File.WriteAllBytes(temp,bytes);
            if(File.Exists(path)) File.Delete(path);
            File.Move(temp,path);
        }

        // Called before the backend starts. Restores or merges the accounts stored inside this exe.
        public void Prepare(Action<string> status) {
            Trailer trailer=ReadTrailer();
            if(trailer==null || trailer.Meta==null) return;
            Directory.CreateDirectory(work);
            bool liveExists=File.Exists(db) && File.Exists(key);
            DataState live=liveExists ? Inspect(db) : new DataState();
            if(liveExists && live.Digest==trailer.Meta.Digest) { WriteSync(live.Digest); return; }
            if(!liveExists || live.Accounts==0) {
                if(trailer.Meta.Accounts==0) return;
                status("Akkauntlar fayldan tiklanmoqda...");
                BackupLive("restore");
                byte[] dbBytes, keyBytes;
                using(var zip=new ZipArchive(new MemoryStream(trailer.Blob),ZipArchiveMode.Read)) { dbBytes=Entry(zip,"manager.sqlite3"); keyBytes=Entry(zip,"vault.key"); }
                Directory.CreateDirectory(data);
                foreach(string suffix in new [] {"-wal","-shm","-journal"}) { try { if(File.Exists(db+suffix)) File.Delete(db+suffix); } catch { } }
                WriteAtomic(key,keyBytes); WriteAtomic(db,dbBytes);
                WriteSync(trailer.Meta.Digest);
                Log("restored "+trailer.Meta.Accounts+" accounts from exe");
                return;
            }
            status("Akkauntlar birlashtirilmoqda...");
            bool liveUnchanged=ReadSync()==live.Digest;
            BackupLive("merge");
            Merge(trailer.Blob,liveUnchanged);
        }

        string Login(byte[] plain,out string password) {
            var credential=Json.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(plain));
            password=Text(credential["password"]);
            return Text(credential["login"]).Trim().ToLowerInvariant();
        }

        void Merge(byte[] blob,bool preferIncoming) {
            byte[] incomingDb, incomingKey, liveKey=File.ReadAllBytes(key);
            using(var zip=new ZipArchive(new MemoryStream(blob),ZipArchiveMode.Read)) { incomingDb=Entry(zip,"manager.sqlite3"); incomingKey=Entry(zip,"vault.key"); }
            string incoming=Path.Combine(work,"incoming.sqlite3"); File.WriteAllBytes(incoming,incomingDb);
            const string accountSelect="SELECT id,label,hex(secret) AS s,group_id FROM accounts ORDER BY rowid";
            var incomingAccounts=sql.Query(incoming,work,accountSelect);
            var incomingGroups=sql.Query(incoming,work,"SELECT id,name FROM account_groups ORDER BY rowid");
            var liveAccounts=sql.Query(db,work,accountSelect);
            var liveGroups=sql.Query(db,work,"SELECT id,name FROM account_groups ORDER BY rowid");
            var script=new StringBuilder(".bail on\nBEGIN;\n");

            var groupIds=new HashSet<string>(); var groupByName=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase); var groupMap=new Dictionary<string,string>();
            foreach(var group in liveGroups) { groupIds.Add(Text(group["id"])); groupByName[Text(group["name"])]=Text(group["id"]); }
            foreach(var group in incomingGroups) {
                string id=Text(group["id"]), name=Text(group["name"]), target;
                if(!groupByName.TryGetValue(name,out target)) {
                    target=groupIds.Contains(id) ? NewId() : id; groupIds.Add(target); groupByName[name]=target;
                    script.Append("INSERT INTO account_groups(id,name) VALUES(").Append(Quote(target)).Append(',').Append(Quote(name)).Append(");\n");
                }
                groupMap[id]=target;
            }

            var byLogin=new Dictionary<string,KeyValuePair<string,string>>(); var usedIds=new HashSet<string>();
            foreach(var account in liveAccounts) {
                usedIds.Add(Text(account["id"]));
                try { string password; string login=Login(Fernet.Decrypt(liveKey,FromHex(Text(account["s"]))),out password); if(!byLogin.ContainsKey(login)) byLogin[login]=new KeyValuePair<string,string>(Text(account["id"]),password); }
                catch(Exception) { }
            }

            int added=0, updated=0, kept=0, failed=0;
            foreach(var account in incomingAccounts) {
                try {
                    byte[] plain=Fernet.Decrypt(incomingKey,FromHex(Text(account["s"])));
                    string password; string login=Login(plain,out password);
                    string secret=Hex(Fernet.Encrypt(liveKey,plain));
                    string label=Text(account["label"]), sourceGroup=Text(account["group_id"]), group;
                    if(!groupMap.TryGetValue(sourceGroup,out group)) group="default";
                    KeyValuePair<string,string> existing;
                    if(byLogin.TryGetValue(login,out existing)) {
                        if(preferIncoming && existing.Value!=password) {
                            script.Append("UPDATE accounts SET label=").Append(Quote(label)).Append(",secret=X'").Append(secret).Append("',group_id=").Append(Quote(group)).Append(",status='idle',detail='',updated=NULL WHERE id=").Append(Quote(existing.Key)).Append(";\n");
                            updated++;
                        } else kept++;
                        continue;
                    }
                    string id=Text(account["id"]); if(usedIds.Contains(id)) id=NewId(); usedIds.Add(id);
                    script.Append("INSERT INTO accounts(id,label,secret,status,detail,group_id) VALUES(").Append(Quote(id)).Append(',').Append(Quote(label)).Append(",X'").Append(secret).Append("','idle','',").Append(Quote(group)).Append(");\n");
                    byLogin[login]=new KeyValuePair<string,string>(id,password); added++;
                } catch(Exception) { failed++; }
            }
            script.Append("COMMIT;\n");
            sql.Run(db,work,script.ToString());
            if(sql.LastExit!=0) throw new IOException("Merge failed: "+sql.LastError);
            Log("merged: added="+added+" updated="+updated+" kept="+kept+" failed="+failed);
            try { File.Delete(incoming); } catch { }
        }

        // Called after the backend has stopped. Queues an in-place rewrite of the trailer in the exe.
        public void Save(string saverPath) {
            if(!File.Exists(db) || !File.Exists(key) || !File.Exists(saverPath) || !File.Exists(exe)) return;
            Directory.CreateDirectory(work);
            string snapshot=Path.Combine(work,"snapshot.sqlite3");
            foreach(string suffix in new [] {"","-wal","-shm","-journal"}) { try { if(File.Exists(snapshot+suffix)) File.Delete(snapshot+suffix); } catch { } }
            sql.Run(db,work,".backup 'snapshot.sqlite3'");
            if(sql.LastExit!=0 || !File.Exists(snapshot)) { Log("snapshot failed"); return; }
            DataState state=Inspect(snapshot);
            if(state.Accounts==0) return;
            Trailer current=ReadTrailer();
            if(current!=null && current.Meta!=null && current.Meta.Digest==state.Digest) { WriteSync(state.Digest); return; }
            sql.Run(snapshot,work,"DELETE FROM logs;\nDELETE FROM browser_sessions;\nUPDATE settings SET enabled=0;\nVACUUM;");
            var memory=new MemoryStream();
            using(var zip=new ZipArchive(memory,ZipArchiveMode.Create,true)) {
                Add(zip,"manager.sqlite3",File.ReadAllBytes(snapshot));
                Add(zip,"vault.key",File.ReadAllBytes(key));
                var meta=new Dictionary<string,object> {{"savedAt",DateTime.UtcNow.ToString("o")},{"digest",state.Digest},{"accounts",state.Accounts},{"app","1.2.0"}};
                Add(zip,"meta.json",new UTF8Encoding(false).GetBytes(Json.Serialize(meta)));
            }
            if(current!=null) KeepPreviousTrailer(current);
            string pending=Path.Combine(data,"pending-save.emk");
            WriteAtomic(pending,memory.ToArray());
            var info=new ProcessStartInfo(saverPath,"\""+exe+"\" \""+pending+"\" "+Process.GetCurrentProcess().Id) {UseShellExecute=false,CreateNoWindow=true};
            Process.Start(info);
            WriteSync(state.Digest);
            Log("queued save of "+state.Accounts+" accounts into exe");
            try { File.Delete(snapshot); } catch { }
        }

        void KeepPreviousTrailer(Trailer previous) {
            try {
                string root=Path.Combine(data,"portable-backups"); Directory.CreateDirectory(root);
                File.WriteAllBytes(Path.Combine(root,"trailer-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+".emk"),previous.Blob);
                var files=new List<string>(Directory.GetFiles(root,"trailer-*.emk")); files.Sort();
                for(int i=0;i<files.Count-3;i++) try { File.Delete(files[i]); } catch { }
            } catch { }
        }
    }
}
