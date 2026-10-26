using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>
/// Channels of a PDA3 DAT that is not the validated model (for example a file written by ibaPDA).
/// Header and index are not readable, so channel names are unknown and the block of each channel is rebuilt:
/// <list type="bullet">
/// <item>every channel ends with one short block (its final flush): the trailing short blocks give the number of channels, in order;</item>
/// <item>all channels have the same number of samples (total / channels) over the header duration;</item>
/// <item>full blocks are flushed in time order: the sample position where a block ends grows along the file (a few samples of tolerance);</item>
/// <item>the jump between consecutive blocks of a channel must be comparable with the largest step inside the blocks (plausibility);
/// only when no assignment respects it the rule is dropped;</item>
/// <item>among the valid assignments the one with the smallest jumps is chosen; it must be clearly better than the second one,
/// otherwise the file is refused as ambiguous.</item>
/// </list>
/// </summary>
public sealed record Pda3Layout(int Channels, long Samples, long PeriodTicks, int[][] ChannelBlocks, double BestCost, double? SecondCost, bool Exhaustive, bool Strict)
{
    public double PeriodMs => PeriodTicks / 10000.0;

    const double ShortBlock = 0.97;
    const long MaxSteps = 5_000_000;

    public static Pda3Layout Build(Pda3Reader reader)
    {
        var blocks = reader.Blocks;
        if (blocks.Count == 0) throw new UserError("PDA3: nessun blocco dati");
        var big = blocks.Max(b => b.Size);
        var finals = new List<int>();
        for (var i = blocks.Count - 1; i >= 0 && blocks[i].Size < ShortBlock * big; i--) finals.Insert(0, i);
        if (finals.Count == 0 || blocks.Take(blocks.Count - finals.Count).Any(b => b.Size < ShortBlock * big))
            throw new UserError("PDA3: struttura dei blocchi non riconosciuta (blocchi finali dei canali non individuabili)");
        var channels = finals.Count;
        var total = blocks.Sum(b => b.Samples);
        if (total % channels != 0) throw new UserError($"PDA3: {total} campioni non divisibili tra {channels} canali");
        var samples = total / channels;
        var span = (long)(reader.EndTicks - reader.StartTicks);
        if (samples < 1 || span % samples != 0 || span / samples < 1) throw new UserError("PDA3: periodo di campionamento non ricavabile dalla durata del file");
        var period = span / samples;
        var target = finals.Select(f => samples - blocks[f].Samples).ToArray();
        if (target.Any(t => t < 0)) throw new UserError("PDA3: blocco finale più lungo della durata del canale");
        var ends = blocks.Select(reader.Ends).ToArray();
        var steps = blocks.Select(b => MaxStep(reader, b)).ToArray();
        var tolerance = Math.Max(16, samples / 1000);
        // First with the plausibility limit on the jumps between blocks, then without it (a step change exactly at a block boundary).
        var nonFinal = Enumerable.Range(0, blocks.Count).Where(i => !finals.Contains(i)).ToArray();
        var strict = true;
        var result = Search(blocks, finals, nonFinal, target, ends, steps, tolerance, strict: true);
        if (result == null)
        {
            strict = false;
            result = Search(blocks, finals, nonFinal, target, ends, steps, tolerance, strict: false);
        }
        if (result == null) throw new UserError("PDA3: impossibile attribuire i blocchi ai canali in modo coerente; variante del file non supportata");
        var (best, bestCost, alternative, exhaustive) = result.Value;
        if (alternative is double other && other - bestCost < Math.Max(1.0, bestCost))
            throw new UserError("PDA3: attribuzione dei blocchi ai canali ambigua; il file non può essere letto in modo affidabile");
        var lists = Enumerable.Range(0, channels).Select(_ => new List<int>()).ToArray();
        for (var i = 0; i < nonFinal.Length; i++) lists[best[i]].Add(nonFinal[i]);
        for (var k = 0; k < channels; k++) lists[k].Add(finals[k]);
        return new Pda3Layout(channels, samples, period, lists.Select(l => l.ToArray()).ToArray(), bestCost, alternative, exhaustive, strict);
    }

    /// <summary>Largest change between consecutive samples of a block (the natural variation of the signal).</summary>
    static double MaxStep(Pda3Reader reader, Pda3Reader.Block block)
    {
        double step = 0, previous = double.NaN;
        foreach (var v in reader.Values(block))
        {
            if (double.IsFinite(v) && double.IsFinite(previous)) step = Math.Max(step, Math.Abs(v - previous));
            previous = v;
        }
        return step;
    }

    static double Jump(double a, double b) => double.IsFinite(a) && double.IsFinite(b) ? Math.Abs(a - b) : 0;

    /// <summary>
    /// Chains of blocks are built in file order without channel labels (a block extends a chain or opens a new one), then each chain is matched
    /// to the final block of a channel: its samples must equal the channel's samples before the final block. Iterative depth-first search
    /// with branch and bound keeping the two best complete solutions; the result gives the channel of each non-final block in file order.
    /// </summary>
    static (int[] best, double cost, double? second, bool exhaustive)? Search(List<Pda3Reader.Block> blocks, List<int> finals, int[] order, long[] target,
        (double first, double last)[] ends, double[] stepOf, long tolerance, bool strict)
    {
        var channels = finals.Count;
        var open = Enumerable.Range(0, channels).Where(k => target[k] > 0).ToArray();
        var targets = open.Select(k => target[k]).ToHashSet();
        var maxTarget = open.Length == 0 ? 0 : open.Max(k => target[k]);
        var m = order.Length;
        var largest = new long[m + 1];
        for (var i = m - 1; i >= 0; i--) largest[i] = Math.Max(largest[i + 1], blocks[order[i]].Samples);
        var cum = new long[open.Length];
        var last = new double[open.Length];
        var lastStep = new double[open.Length];
        var chains = 0;
        var choice = new int[m];
        var candidates = new int[m][];
        var position = new int[m];
        var accAt = new double[m + 1];
        var endAt = new long[m + 1];
        var saved = new (long cum, double last, double step, int chains)[m];
        int[]? best = null;
        double bestCost = double.PositiveInfinity, second = double.PositiveInfinity;
        long steps = 0;
        var exhaustive = true;

        bool Plausible(double from, double fromStep, int block)
        {
            if (!strict) return true;
            var to = ends[block].first;
            return Jump(from, to) <= 4 * Math.Max(fromStep, stepOf[block]) + 1e-9 * Math.Max(1, Math.Abs(to));
        }
        double Cost(int c, int block) => c < chains ? Jump(last[c], ends[block].first) : 0;
        int[] Candidates(int level)
        {
            var b = order[level];
            var n = blocks[b].Samples;
            // A chain that cannot reach the current flush position any more must already be complete.
            for (var c = 0; c < chains; c++)
                if (cum[c] + largest[level] < endAt[level] - tolerance && !targets.Contains(cum[c])) return [];
            var list = new List<int>();
            for (var c = 0; c < chains; c++)
                if (cum[c] + n <= maxTarget && cum[c] + n >= endAt[level] - tolerance && Plausible(last[c], lastStep[c], b)) list.Add(c);
            list.Sort((x, y) => Cost(x, b).CompareTo(Cost(y, b)));
            if (chains < open.Length && n >= endAt[level] - tolerance) list.Add(chains);
            return list.ToArray();
        }
        // Chains to channels: equal samples and plausible join with the final block; two cheapest matchings.
        void Complete(double acc)
        {
            if (chains != open.Length) return;
            var matched = new int[chains];
            var taken = new bool[open.Length];
            void Match(int c, double total)
            {
                if (total >= second) return;
                if (c == chains)
                {
                    if (total < bestCost)
                    {
                        second = bestCost; bestCost = total;
                        best = choice.Select(x => open[matched[x]]).ToArray();
                    }
                    else if (total < second) second = total;
                    return;
                }
                for (var j = 0; j < open.Length; j++)
                {
                    if (taken[j] || target[open[j]] != cum[c] || !Plausible(last[c], lastStep[c], finals[open[j]])) continue;
                    taken[j] = true; matched[c] = j;
                    Match(c + 1, total + Jump(last[c], ends[finals[open[j]]].first));
                    taken[j] = false;
                }
            }
            Match(0, acc);
        }

        if (m == 0) { Complete(0); return best == null ? null : (best, bestCost, double.IsInfinity(second) ? null : second, true); }
        var level = 0;
        candidates[0] = Candidates(0);
        while (level >= 0)
        {
            if (++steps > MaxSteps) { exhaustive = false; break; }
            if (position[level] < candidates[level].Length && accAt[level] < second)
            {
                var c = candidates[level][position[level]++];
                var b = order[level];
                var cost = Cost(c, b);
                saved[level] = c < chains ? (cum[c], last[c], lastStep[c], chains) : (0, 0, 0, chains);
                if (c == chains) chains++;
                choice[level] = c;
                cum[c] += blocks[b].Samples; last[c] = ends[b].last; lastStep[c] = stepOf[b];
                accAt[level + 1] = accAt[level] + cost;
                endAt[level + 1] = Math.Max(endAt[level], cum[c]);
                if (level + 1 == m)
                {
                    Complete(accAt[m]);
                    Undo(level);
                    continue;
                }
                level++;
                candidates[level] = Candidates(level);
                position[level] = 0;
                continue;
            }
            level--;
            if (level >= 0) Undo(level);
        }
        void Undo(int at)
        {
            var c = choice[at];
            (cum[c], last[c], lastStep[c], chains) = saved[at];
        }
        if (best == null) return null;
        return (best, bestCost, double.IsInfinity(second) ? null : second, exhaustive);
    }
}
