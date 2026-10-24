namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// Disk-backed sort with bounded RAM: sorted runs in temporary files, then a k-way
/// merge. Equal keys keep insertion order (as an SQLite index on (key, rowid)).
/// </summary>
public sealed class ExternalSort<T> : IDisposable
{
    readonly Comparison<T> compare;
    readonly Action<BinaryWriter, T> write;
    readonly Func<BinaryReader, T> read;
    readonly string directory;
    readonly int runSize;
    readonly List<(T item, long order)> buffer = new();
    readonly List<string> runs = new();
    long order;

    public ExternalSort(string directory, Comparison<T> compare, Action<BinaryWriter, T> write, Func<BinaryReader, T> read, int runSize = 1_000_000)
    {
        this.directory = directory; this.compare = compare; this.write = write; this.read = read; this.runSize = runSize;
    }

    int Compare((T item, long order) a, (T item, long order) b)
    {
        var c = compare(a.item, b.item);
        return c != 0 ? c : a.order.CompareTo(b.order);
    }

    public void Add(T item)
    {
        buffer.Add((item, order++));
        if (buffer.Count >= runSize) Spill();
    }

    void Spill()
    {
        buffer.Sort(Compare);
        var path = Path.Combine(directory, ".sort_" + Guid.NewGuid().ToString("N") + ".run");
        using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1 << 20, FileOptions.None))
        using (var writer = new BinaryWriter(file))
        {
            foreach (var (item, ordinal) in buffer) { writer.Write(ordinal); write(writer, item); }
        }
        runs.Add(path);
        buffer.Clear();
    }

    public IEnumerable<T> Sorted()
    {
        if (runs.Count == 0)
        {
            buffer.Sort(Compare);
            foreach (var (item, _) in buffer) yield return item;
            yield break;
        }
        if (buffer.Count > 0) Spill();
        var readers = runs.Select(p => new BinaryReader(new FileStream(p, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20))).ToList();
        try
        {
            var heap = new PriorityQueue<int, (T item, long order)>(Comparer<(T item, long order)>.Create(Compare));
            var heads = new (T item, long order)[readers.Count];
            bool Next(int i)
            {
                var stream = readers[i].BaseStream;
                if (stream.Position >= stream.Length) return false;
                var ordinal = readers[i].ReadInt64();
                heads[i] = (read(readers[i]), ordinal);
                return true;
            }
            for (var i = 0; i < readers.Count; i++) if (Next(i)) heap.Enqueue(i, heads[i]);
            while (heap.TryDequeue(out var index, out var head))
            {
                yield return head.item;
                if (Next(index)) heap.Enqueue(index, heads[index]);
            }
        }
        finally { foreach (var r in readers) r.Dispose(); }
    }

    public void Dispose()
    {
        foreach (var path in runs)
        {
            try { File.Delete(path); } catch (IOException) { }
        }
        runs.Clear();
        buffer.Clear();
    }
}
