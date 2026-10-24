using System.Globalization;
using System.Text;

namespace SpaceAnalyzer.App.Infrastructure;

/// <summary>Rotating application log (5 MiB × 5 backups, same line format as the 2.x logging module).</summary>
public static class Log
{
    static readonly object Gate = new();
    static string? path;
    const long MaxBytes = 5 * 1024 * 1024;
    const int Backups = 5;

    public static void Configure(string file)
    {
        lock (Gate) path = file;
        Directory.CreateDirectory(Path.GetDirectoryName(file)!);
    }

    public static void Info(string message) => Write("INFO", message);
    public static void Warning(string message) => Write("WARNING", message);
    public static void Error(string message, Exception? exc = null) => Write("ERROR", exc == null ? message : message + "\n" + exc);

    static void Write(string level, string message)
    {
        lock (Gate)
        {
            if (path == null) return;
            try
            {
                var now = DateTime.Now;
                var line = now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + "," + now.Millisecond.ToString("000", CultureInfo.InvariantCulture)
                    + " " + level + " root " + message + "\n";
                var info = new FileInfo(path);
                if (info.Exists && info.Length + Encoding.UTF8.GetByteCount(line) > MaxBytes) Rotate();
                File.AppendAllText(path, line, new UTF8Encoding(false));
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    static void Rotate()
    {
        for (var i = Backups - 1; i >= 1; i--)
        {
            var source = path + "." + i;
            if (File.Exists(source)) File.Move(source, path + "." + (i + 1), true);
        }
        File.Move(path!, path + ".1", true);
    }
}
