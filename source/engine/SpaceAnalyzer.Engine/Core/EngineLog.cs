namespace SpaceAnalyzer.Engine.Core;

/// <summary>Application log of the hosting process (the 2.x logging.info lines of the recorders).</summary>
public static class EngineLog
{
    public static Action<string>? Sink { get; set; }

    public static void Info(string message)
    {
        try { Sink?.Invoke(message); } catch (Exception) { }
    }
}
