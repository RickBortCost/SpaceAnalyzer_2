using System.Globalization;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Renci.SshNet;
using Renci.SshNet.Common;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Ssh;

/// <summary>Independent SSH2 worker: bounded console, explicit host trust, no credential persistence (ssh_service.py).</summary>
public sealed class SshService(string directory, long outputLimit = 2 * 1024 * 1024)
{
    static readonly Dictionary<string, object?> DefaultProfile = new()
    {
        ["host"] = "192.168.5.220", ["port"] = 22L, ["username"] = "Administrator", ["auth"] = "password", ["connect_timeout"] = 10L, ["keepalive"] = 20L, ["encoding"] = "utf-8",
    };
    const int MaxText = 128 * 1024;

    readonly object gate = new();
    SshClient? client;
    SshCommand? command;
    ShellStream? shell;
    Thread? connectThread, runThread;
    long generation;
    CancellationTokenSource cancel = new();
    ManualResetEventSlim trustEvent = new(false);
    ManualResetEventSlim stopEvent = new(false);
    bool trustDecision;
    Dictionary<string, object?>? pending;
    string state = "disconnected", error = "";
    Dictionary<string, object?> target = new(), job = new();
    Dictionary<string, object?> lastConfig = new(DefaultProfile);
    readonly LinkedList<Dictionary<string, object?>> events = new();
    long outputBytes, seq, droppedBytes;

    static string Now() => DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fff", CultureInfo.InvariantCulture) + "+00:00";

    static long Integer(object? value, long lo, long hi, string label)
    {
        double n;
        switch (value)
        {
            case long l: n = l; break;
            case double d: n = d; break;
            case string s when Py.TryFloat(s, out var parsed): n = parsed; break;
            default: throw new UserError($"{label}: intero tra {lo} e {hi}");
        }
        if (!double.IsFinite(n) || n % 1 != 0 || n < lo || n > hi) throw new UserError($"{label}: intero tra {lo} e {hi}");
        return (long)n;
    }

    public static Dictionary<string, object?> Profile(object? data)
    {
        if (data is not Dictionary<string, object?> d) throw new UserError("Parametri SSH non validi");
        var cfg = DefaultProfile.ToDictionary(k => k.Key, k => d.TryGetValue(k.Key, out var v) ? v : k.Value);
        foreach (var key in new[] { "host", "username" })
        {
            if (cfg[key] is not string s || s.Trim().Length == 0 || s.Length > 253 || Regex.IsMatch(s.Trim(), @"[\s/\x00-\x1f]"))
                throw new UserError("SSH: IP/hostname e utente richiesti, senza spazi o URL");
            cfg[key] = s.Trim();
        }
        cfg["port"] = Integer(cfg["port"], 1, 65535, "Porta SSH");
        cfg["connect_timeout"] = Integer(cfg["connect_timeout"], 2, 60, "Timeout SSH");
        cfg["keepalive"] = Integer(cfg["keepalive"], 5, 300, "Keepalive SSH");
        if (cfg["auth"] is not ("password" or "key")) throw new UserError("Autenticazione SSH: password o chiave");
        if (cfg["encoding"] is not ("utf-8" or "cp1252" or "latin-1")) throw new UserError("Codifica SSH non supportata");
        return cfg;
    }

    Dictionary<string, object?> Read(string name, Dictionary<string, object?> fallback)
    {
        var path = Path.Combine(directory, name);
        if (!File.Exists(path)) return new(fallback);
        try
        {
            if (Py.Plain(JsonNode.Parse(File.ReadAllText(path, Encoding.UTF8))) is Dictionary<string, object?> data) return data;
        }
        catch (Exception exc) when (exc is IOException or System.Text.Json.JsonException) { }
        throw new UserError("Impostazioni/chiavi SSH non leggibili: verificare " + name);
    }

    void Write(string name, object data) => Py.AtomicPlain(Path.Combine(directory, name), data);

    public Dictionary<string, object?> Config() { lock (gate) return Profile(Read("ssh_profile.json", DefaultProfile)); }

    public Dictionary<string, object?> SaveConfig(object? data)
    {
        var cfg = Profile(data);
        lock (gate) Write("ssh_profile.json", cfg);
        return cfg;
    }

    static string HostId(Dictionary<string, object?> cfg) => "[" + Py.DumpsPlain(((string)cfg["host"]!).ToLowerInvariant()) + "," + cfg["port"] + "]";

    public object? KnownKey(Dictionary<string, object?> cfg)
    {
        lock (gate) return Read("ssh_known_hosts.json", new()).GetValueOrDefault(HostId(cfg));
    }

    public void ForgetKey(Dictionary<string, object?> data)
    {
        var cfg = Profile(data);
        lock (gate)
        {
            if (state is "connected" or "connecting" or "awaiting_trust") throw new UserError("Disconnetti SSH prima di rimuovere la chiave salvata");
            var keys = Read("ssh_known_hosts.json", new());
            if (keys.GetValueOrDefault(HostId(cfg)) is not Dictionary<string, object?> item || !Equals(data.GetValueOrDefault("fingerprint"), item.GetValueOrDefault("fingerprint")))
                throw new UserError("Chiave cambiata/non trovata: aggiornare lo stato");
            keys.Remove(HostId(cfg));
            Write("ssh_known_hosts.json", keys);
        }
    }

    public Dictionary<string, object?> Connect(Dictionary<string, object?> data)
    {
        var cfg = Profile(data);
        AuthenticationMethod auth;
        var username = (string)cfg["username"]!;
        if ((string)cfg["auth"]! == "password")
        {
            if (data.GetValueOrDefault("password") is not string password || password.Length is < 1 or > 4096) throw new UserError("Inserire la password SSH");
            auth = new PasswordAuthenticationMethod(username, password);
        }
        else
        {
            var phrase = data.GetValueOrDefault("passphrase") ?? "";
            if (phrase is not string passphrase || passphrase.Length > 4096) throw new UserError("Passphrase non valida");
            auth = new PrivateKeyAuthenticationMethod(username, PrivateKey(data.GetValueOrDefault("private_key"), passphrase));
        }
        lock (gate)
        {
            if (state is "connected" or "connecting" or "awaiting_trust" || connectThread is { IsAlive: true }) throw new UserError("Sessione SSH già aperta/in corso: disconnettere prima");
            SaveConfig(cfg);
            lastConfig = cfg;
            var gen = ++generation;
            cancel = new CancellationTokenSource();
            trustEvent = new ManualResetEventSlim(false);
            trustDecision = false;
            pending = null;
            target = new();
            state = "connecting";
            error = "";
            var token = cancel.Token;
            var trust = trustEvent;
            connectThread = new Thread(() => ConnectWorker(cfg, auth, gen, token, trust)) { IsBackground = true, Name = "SSH-Connect" };
            connectThread.Start();
        }
        return Status();
    }

    static PrivateKeyFile PrivateKey(object? text, string passphrase)
    {
        if (text is not string key || Encoding.UTF8.GetByteCount(key) is < 1 or > MaxText) throw new UserError("Chiave privata OpenSSH/PEM richiesta (max 128 KiB)");
        if (key.Contains("PuTTY-User-Key-File")) throw new UserError("Chiave PPK: esportarla in OpenSSH/PEM con PuTTYgen");
        try { return new PrivateKeyFile(new MemoryStream(Encoding.UTF8.GetBytes(key)), passphrase.Length > 0 ? passphrase : null); }
        catch (SshPassPhraseNullOrEmptyException) { throw new UserError("La chiave privata richiede una passphrase"); }
        catch (Exception) { throw new UserError("Chiave privata non supportata o passphrase errata: usare RSA, Ed25519 o ECDSA OpenSSH/PEM"); }
    }

    void ConnectWorker(Dictionary<string, object?> cfg, AuthenticationMethod auth, long gen, CancellationToken token, ManualResetEventSlim trust)
    {
        SshClient? ssh = null;
        var success = false;
        try
        {
            var timeout = TimeSpan.FromSeconds((long)cfg["connect_timeout"]!);
            var info = new Renci.SshNet.ConnectionInfo((string)cfg["host"]!, (int)(long)cfg["port"]!, (string)cfg["username"]!, auth) { Timeout = timeout };
            ssh = new SshClient(info);
            string? hostError = null;
            ssh.HostKeyReceived += (_, e) =>
            {
                var blob = e.HostKey;
                var fp = "SHA256:" + Convert.ToBase64String(SHA256.HashData(blob)).TrimEnd('=');
                var algorithm = e.HostKeyName;
                var publicKey = Convert.ToBase64String(blob);
                bool known;
                lock (gate)
                {
                    if (gen != generation || token.IsCancellationRequested) { e.CanTrust = false; return; }
                    var saved = Read("ssh_known_hosts.json", new()).GetValueOrDefault(HostId(cfg)) as Dictionary<string, object?>;
                    target = new()
                    {
                        ["host"] = cfg["host"], ["port"] = cfg["port"], ["username"] = cfg["username"], ["fingerprint"] = fp, ["algorithm"] = algorithm,
                        ["server_version"] = ssh.ConnectionInfo.ServerVersion,
                    };
                    if (saved != null && (!Equals(saved.GetValueOrDefault("algorithm"), algorithm) || !CryptographicOperations.FixedTimeEquals(
                            Encoding.ASCII.GetBytes(saved.GetValueOrDefault("key") as string ?? ""), Encoding.ASCII.GetBytes(publicKey))))
                    {
                        hostError = "Chiave host SSH diversa da quella salvata. Verifica il target; rimuovi esplicitamente la vecchia chiave solo se il cambio è previsto.";
                        e.CanTrust = false;
                        return;
                    }
                    known = saved != null;
                    if (!known)
                    {
                        state = "awaiting_trust";
                        pending = new(target) { ["id"] = Convert.ToBase64String(RandomNumberGenerator.GetBytes(24)).TrimEnd('=').Replace('+', '-').Replace('/', '_') };
                    }
                }
                if (known) { e.CanTrust = true; return; }
                if (!trust.Wait(TimeSpan.FromSeconds(120))) { hostError = "Verifica impronta SSH scaduta: ripetere la connessione"; e.CanTrust = false; return; }
                lock (gate)
                {
                    if (gen != generation || token.IsCancellationRequested) { e.CanTrust = false; return; }
                    if (!trustDecision) { hostError = "Chiave host SSH non accettata"; e.CanTrust = false; return; }
                    var keys = Read("ssh_known_hosts.json", new());
                    keys[HostId(cfg)] = new Dictionary<string, object?> { ["algorithm"] = algorithm, ["key"] = publicKey, ["fingerprint"] = fp, ["accepted_at"] = Now() };
                    Write("ssh_known_hosts.json", keys);
                    pending = null;
                    state = "connecting";
                }
                e.CanTrust = true;
            };
            lock (gate)
            {
                if (gen != generation || token.IsCancellationRequested) return;
                client = ssh;
            }
            try { ssh.Connect(); }
            catch (SshConnectionException) when (hostError != null) { throw new UserError(hostError); }
            catch (SshException) when (hostError != null) { throw new UserError(hostError); }
            if (!ssh.IsConnected) throw new SshException("Handshake SSH2 non completato");
            ssh.KeepAliveInterval = TimeSpan.FromSeconds((long)cfg["keepalive"]!);
            lock (gate)
            {
                if (gen != generation || token.IsCancellationRequested) return;
                state = "connected";
                target["connected_at"] = Now();
                target["cipher"] = ssh.ConnectionInfo.CurrentServerEncryption;
                error = "";
            }
            Log.Info($"SSH connected host={cfg["host"]} port={cfg["port"]}");
            success = true;
        }
        catch (Exception exc)
        {
            var message = exc switch
            {
                SshAuthenticationException => "Autenticazione SSH non riuscita: controllare utente, password/chiave e permessi del server",
                SshOperationTimeoutException or TimeoutException => "Timeout SSH: controllare IP, porta, rete e servizio sshd",
                SocketException or IOException => "Connessione SSH non disponibile: controllare IP, porta, rete e servizio sshd",
                UserError => exc.Message,
                _ => "Errore protocollo SSH2: " + (exc.Message.Length > 300 ? exc.Message[..300] : exc.Message),
            };
            lock (gate)
                if (gen == generation && !token.IsCancellationRequested) { state = "error"; error = message; pending = null; client = null; }
            Log.Warning("SSH connection failed type=" + exc.GetType().Name);
        }
        finally
        {
            if (!success || token.IsCancellationRequested)
            {
                lock (gate) if (client == ssh && !success) client = null;
                try { ssh?.Dispose(); } catch (Exception) { }
            }
        }
    }

    public void Trust(Dictionary<string, object?> data)
    {
        lock (gate)
        {
            if (state != "awaiting_trust" || pending == null || !Equals(data.GetValueOrDefault("id"), pending["id"]) || !Equals(data.GetValueOrDefault("fingerprint"), pending["fingerprint"]))
                throw new UserError("Verifica SSH scaduta: aggiornare lo stato");
            if (data.GetValueOrDefault("accept") is not bool accept) throw new UserError("Specificare accetta o rifiuta chiave");
            trustDecision = accept;
            trustEvent.Set();
        }
    }

    public void Disconnect()
    {
        SshClient? ssh;
        lock (gate)
        {
            generation++;
            cancel.Cancel();
            trustEvent.Set();
            stopEvent.Set();
            ssh = client; client = null;
            CloseChannel();
            state = "disconnected"; pending = null; error = "";
            if (job.GetValueOrDefault("state") is "starting" or "running") { job["state"] = "cancelled"; job["ended_at"] = Now(); job["reason"] = "disconnected"; }
        }
        try { ssh?.Dispose(); } catch (Exception) { }
    }

    void CloseChannel()
    {
        try { command?.CancelAsync(); } catch (Exception) { }
        try { shell?.Close(); } catch (Exception) { }
    }

    public void Close()
    {
        Disconnect();
        connectThread?.Join(5000);
        runThread?.Join(5000);
    }

    void Append(string ident, string stream, string text)
    {
        if (text.Length == 0) return;
        lock (gate)
        {
            seq++;
            events.AddLast(new Dictionary<string, object?> { ["seq"] = seq, ["job_id"] = ident, ["stream"] = stream, ["text"] = text, ["at"] = Now() });
            outputBytes += Encoding.UTF8.GetByteCount(text);
            while (events.Count > 0 && (outputBytes > outputLimit || events.Count > 8192))
            {
                var n = Encoding.UTF8.GetByteCount((string)events.First!.Value["text"]!);
                events.RemoveFirst();
                outputBytes -= n; droppedBytes += n;
            }
        }
    }

    public Dictionary<string, object?> Status(object? after = null)
    {
        lock (gate)
        {
            if (state == "connected" && client is not { IsConnected: true }) { state = "error"; error = "Collegamento SSH interrotto; riconnettere esplicitamente"; }
            var r = new Dictionary<string, object?>
            {
                ["state"] = state, ["error"] = error, ["target"] = new Dictionary<string, object?>(target), ["pending"] = pending == null ? null : new Dictionary<string, object?>(pending),
                ["job"] = new Dictionary<string, object?>(job), ["last_seq"] = seq, ["first_seq"] = events.First?.Value["seq"] ?? seq + 1,
                ["dropped_bytes"] = droppedBytes, ["output_limit_bytes"] = outputLimit,
            };
            if (after != null)
            {
                var position = Integer(after, 0, (1L << 53) - 1, "Posizione output");
                var list = new List<object?>();
                long size = 0;
                foreach (var item in events)
                {
                    if ((long)item["seq"]! <= position) continue;
                    list.Add(new Dictionary<string, object?>(item));
                    size += ((string)item["text"]!).Length;
                    if (list.Count >= 200 || size >= 128 * 1024) break;
                }
                r["events"] = list;
                r["next_seq"] = list.Count > 0 ? ((Dictionary<string, object?>)list[^1]!)["seq"] : Math.Max(position, seq);
                r["gap"] = events.Count > 0 && position < (long)events.First!.Value["seq"]! - 1;
            }
            return r;
        }
    }

    public Dictionary<string, object?> Execute(Dictionary<string, object?> data)
    {
        var mode = data.GetValueOrDefault("mode") ?? "command";
        var text = data.GetValueOrDefault("text") ?? "";
        var pty = data.GetValueOrDefault("pty") ?? false;
        var timeout = Integer(data.GetValueOrDefault("timeout") ?? 300L, 0, 86400, "Durata massima (s)");
        if (mode is not ("command" or "script" or "shell") || pty is not bool usePty) throw new UserError("Modalità comando/script/shell non valida");
        if (text is not string body || body.Contains('\0') || Encoding.UTF8.GetByteCount(body) > MaxText || mode as string != "shell" && body.Trim().Length == 0)
            throw new UserError("Inserire comando/script (max 128 KiB, senza NUL)");
        var interpreter = data.GetValueOrDefault("interpreter") ?? "/bin/sh";
        if (mode as string == "script" && (interpreter is not string path || !Regex.IsMatch(path, @"^/[A-Za-z0-9_./+-]{1,200}$")))
            throw new UserError("Interprete: percorso assoluto senza argomenti, es. /bin/sh");
        lock (gate)
        {
            if ((string)Status()["state"]! != "connected") throw new UserError("Connettere SSH prima di eseguire");
            if (runThread is { IsAlive: true }) throw new UserError("Un comando SSH è già in esecuzione: attendere o premere Stop");
            var ident = Convert.ToHexString(RandomNumberGenerator.GetBytes(12)).ToLowerInvariant();
            stopEvent = new ManualResetEventSlim(false);
            job = new()
            {
                ["id"] = ident, ["mode"] = mode, ["pty"] = usePty || (string)mode == "shell", ["state"] = "starting", ["started_at"] = Now(), ["ended_at"] = null,
                ["exit_code"] = null, ["reason"] = "", ["error"] = "", ["bytes_received"] = 0L, ["timeout"] = timeout,
            };
            var gen = generation;
            var ssh = client!;
            var stop = stopEvent;
            runThread = new Thread(() => Run(ident, gen, ssh, stop, (string)mode, body, (string)interpreter, usePty, timeout)) { IsBackground = true, Name = "SSH-Console" };
            runThread.Start();
            return new(job);
        }
    }

    Encoding OutputEncoding() => (string)lastConfig["encoding"]! switch
    {
        "cp1252" => Engine.Recording.Legacy.Cp1252Replace,
        "latin-1" => Encoding.Latin1,
        _ => new UTF8Encoding(false, false),
    };

    void Run(string ident, long gen, SshClient ssh, ManualResetEventSlim stop, string mode, string text, string interpreter, bool pty, long timeout)
    {
        var deadline = timeout > 0 ? DateTime.UtcNow.AddSeconds(timeout) : DateTime.MaxValue;
        string finalState = "finished", reason = "", failure = "";
        int? code = null;
        long total = 0;
        var expired = false;
        var decoders = new Dictionary<string, Decoder> { ["stdout"] = OutputEncoding().GetDecoder(), ["stderr"] = OutputEncoding().GetDecoder() };
        using var watchdog = timeout > 0 ? new Timer(_ =>
        {
            expired = true;
            stop.Set();
            lock (gate) if (Equals(job.GetValueOrDefault("id"), ident)) CloseChannel();
        }, null, TimeSpan.FromSeconds(timeout), Timeout.InfiniteTimeSpan) : null;
        string Decode(string stream, byte[] raw, int count, bool flush = false)
        {
            var decoder = decoders[stream];
            var chars = new char[decoder.GetCharCount(raw, 0, count, flush)];
            decoder.GetChars(raw, 0, count, chars, 0, flush);
            return new string(chars);
        }
        void Progress() { lock (gate) if (Equals(job.GetValueOrDefault("id"), ident)) job["bytes_received"] = total; }
        try
        {
            lock (gate)
            {
                if (gen != generation || stop.IsSet) { finalState = "cancelled"; reason = "stopped"; return; }
            }
            var buffer = new byte[16384];
            if (mode == "shell")
            {
                var stream = ssh.CreateShellStream("dumb", 120, 40, 0, 0, 65536);
                lock (gate) { shell = stream; job["state"] = "running"; }
                while (true)
                {
                    if (stop.IsSet) { finalState = "cancelled"; reason = "stopped"; break; }
                    if (DateTime.UtcNow >= deadline) { finalState = "timed_out"; reason = "timeout"; break; }
                    var activity = false;
                    while (stream.DataAvailable)
                    {
                        var n = stream.Read(buffer, 0, buffer.Length);
                        if (n <= 0) break;
                        total += n;
                        Append(ident, "stdout", Decode("stdout", buffer, n));
                        activity = true;
                    }
                    Progress();
                    if (!ssh.IsConnected) throw new SshConnectionException("Connessione SSH interrotta durante il comando");
                    if (!stream.CanRead) { reason = "remote_eof"; break; }
                    if (!activity) stop.Wait(10);
                }
            }
            else
            {
                var commandText = mode == "script" ? interpreter + " -s" : text;
                var cmd = ssh.CreateCommand(commandText);
                cmd.CommandTimeout = Timeout.InfiniteTimeSpan;
                lock (gate) { command = cmd; job["state"] = "running"; }
                var execution = cmd.ExecuteAsync();
                if (mode == "script")
                {
                    using var input = cmd.CreateInputStream();
                    var payload = Encoding.UTF8.GetBytes(text.Replace("\r\n", "\n").Replace('\r', '\n') + "\n");
                    input.Write(payload, 0, payload.Length);
                }
                var outputs = new[] { ("stdout", cmd.OutputStream), ("stderr", cmd.ExtendedOutputStream) };
                while (true)
                {
                    if (stop.IsSet) { finalState = "cancelled"; reason = "stopped"; break; }
                    if (DateTime.UtcNow >= deadline) { finalState = "timed_out"; reason = "timeout"; break; }
                    var activity = false;
                    foreach (var (name, stream) in outputs)
                    {
                        var available = stream is PipeStream pipe ? pipe.Length : 0;
                        while (available > 0)
                        {
                            var n = stream.Read(buffer, 0, (int)Math.Min(buffer.Length, available));
                            if (n <= 0) break;
                            available -= n;
                            total += n;
                            Append(ident, name, Decode(name, buffer, n));
                            activity = true;
                        }
                    }
                    Progress();
                    if (execution.IsCompleted && outputs.All(o => o.Item2 is not PipeStream p || p.Length == 0))
                    {
                        code = cmd.ExitStatus;
                        reason = code != null ? "completed" : "remote_eof";
                        if (execution.IsFaulted) throw execution.Exception!.InnerException!;
                        break;
                    }
                    if (!ssh.IsConnected) throw new SshConnectionException("Connessione SSH interrotta durante il comando");
                    if (!activity) stop.Wait(10);
                }
            }
            foreach (var name in decoders.Keys) Append(ident, name, Decode(name, [], 0, true));
        }
        catch (Exception exc)
        {
            if (stop.IsSet) { finalState = expired ? "timed_out" : "cancelled"; reason = expired ? "timeout" : "stopped"; }
            else { finalState = "failed"; failure = "Esecuzione SSH non riuscita: " + (exc.Message.Length > 300 ? exc.Message[..300] : exc.Message); reason = "error"; }
            Log.Warning("SSH execution failed type=" + exc.GetType().Name);
        }
        finally
        {
            if (expired) { finalState = "timed_out"; reason = "timeout"; }
            lock (gate)
            {
                CloseChannel();
                command = null; shell = null;
                if (Equals(job.GetValueOrDefault("id"), ident) && job.GetValueOrDefault("state") as string != "cancelled")
                {
                    job["state"] = finalState; job["ended_at"] = Now(); job["exit_code"] = code == null ? null : (long)code.Value;
                    job["reason"] = reason; job["error"] = failure; job["bytes_received"] = total;
                }
            }
            Log.Info($"SSH job completed mode={mode} state={finalState} exit={(code?.ToString(CultureInfo.InvariantCulture) ?? "None")} bytes={total}");
        }
    }

    public void Stop(object? ident)
    {
        lock (gate)
        {
            if (!Equals(job.GetValueOrDefault("id"), ident) || job.GetValueOrDefault("state") is not ("starting" or "running")) throw new UserError("Comando SSH non più attivo");
            stopEvent.Set();
            CloseChannel();
        }
    }

    public void SendInput(Dictionary<string, object?> data)
    {
        var text = data.GetValueOrDefault("text");
        var kind = data.GetValueOrDefault("kind") ?? "line";
        if (kind is not ("line" or "interrupt" or "eof") || kind as string == "line" && (text is not string line || Encoding.UTF8.GetByteCount(line) > 4096 || line.Contains('\0')))
            throw new UserError("Input SSH: riga di massimo 4096 byte, Ctrl+C o EOF");
        ShellStream? stream;
        lock (gate)
        {
            if (!Equals(data.GetValueOrDefault("id"), job.GetValueOrDefault("id")) || job.GetValueOrDefault("state") as string != "running" || command == null && shell == null)
                throw new UserError("Comando/shell SSH non attivo");
            if ((string)job["mode"]! == "script") throw new UserError("Lo stdin dello script è riservato al contenuto dello script");
            if (kind as string == "interrupt" && job["pty"] is not true) throw new UserError("Ctrl+C richiede PTY; usare Stop per chiudere il canale");
            stream = shell;
            if (stream == null) throw new UserError("Input disponibile nella modalità shell");
        }
        var raw = kind as string == "interrupt" ? "\x03" : kind as string == "eof" ? "\x04" : (string)text! + "\n";
        stream.Write(raw);
        stream.Flush();
    }

    public void Clear()
    {
        lock (gate)
        {
            if (job.GetValueOrDefault("state") is "starting" or "running") throw new UserError("Fermare il comando prima di cancellare la console");
            events.Clear();
            outputBytes = 0;
            droppedBytes = 0;
        }
    }
}
