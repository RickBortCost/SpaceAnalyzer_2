using System.Text.Json;
using SpaceAnalyzer.Engine;
using SpaceAnalyzer.Engine.Ipc;

// Child process of the UI service: requests on stdin, responses/events/rows on stdout.
var host = new Host(Console.OpenStandardInput(), Console.OpenStandardOutput());
var engine = new EngineService(host);
return engine.Run();
