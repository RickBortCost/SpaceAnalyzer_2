using System.Formats.Asn1;
using System.Net;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Config;

/// <summary>OPC UA client certificate (self-signed, application URI in the SAN) and server certificate files.</summary>
public sealed class Certificates(AppPaths paths, ConfigStore config)
{
    public (string uri, bool created) EnsureClient(string? applicationUri = null, bool force = false)
    {
        Directory.CreateDirectory(paths.CertDir);
        var cfg = config.Load();
        var uri = applicationUri is { Length: > 0 } ? applicationUri : cfg["application_uri"].Str() is { Length: > 0 } c ? c : $"urn:{Dns.GetHostName()}:DSiNC:OPCUAWebClient";
        if (File.Exists(paths.CertFile) && File.Exists(paths.KeyFile) && !force)
        {
            try
            {
                using var existing = X509CertificateLoader.LoadCertificate(File.ReadAllBytes(paths.CertFile));
                if (SanValues(existing).uris.Contains(uri)) return (uri, false);
            }
            catch (CryptographicException) { }
        }
        using var key = RSA.Create(2048);
        var subject = new X500DistinguishedNameBuilder();
        subject.AddCommonName(Defaults.AppName);
        subject.AddOrganizationName("DSiNC");
        var request = new CertificateRequest(subject.Build(), key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature | X509KeyUsageFlags.KeyEncipherment, true));
        request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension(new OidCollection { new Oid("1.3.6.1.5.5.7.3.2") }, false));
        var san = new SubjectAlternativeNameBuilder();
        san.AddUri(new Uri(uri));
        san.AddDnsName(Dns.GetHostName());
        request.CertificateExtensions.Add(san.Build());
        var now = DateTimeOffset.UtcNow;
        var serial = RandomNumberGenerator.GetBytes(16);
        serial[0] &= 0x7F;
        using var certificate = request.Create(request.SubjectName, X509SignatureGenerator.CreateForRSA(key, RSASignaturePadding.Pkcs1), now.AddDays(-1), now.AddDays(3650), serial);
        File.WriteAllBytes(paths.CertFile, certificate.RawData);
        File.WriteAllText(paths.KeyFile, new string(PemEncoding.Write("PRIVATE KEY", key.ExportPkcs8PrivateKey())) + "\n");
        cfg["application_uri"] = uri;
        cfg["client_cert"] = "certificates/client.der";
        cfg["client_key"] = "certificates/client-key.pem";
        config.Save(cfg);
        return (uri, true);
    }

    public static X509Certificate2 LoadAny(byte[] data)
    {
        try { return X509CertificateLoader.LoadCertificate(data); }
        catch (CryptographicException)
        {
            var text = Encoding.ASCII.GetString(data);
            var pem = PemEncoding.Find(text);
            return X509CertificateLoader.LoadCertificate(Convert.FromBase64String(text[pem.Base64Data]));
        }
    }

    static (List<string> uris, List<string> dns) SanValues(X509Certificate2 cert)
    {
        var uris = new List<string>(); var dns = new List<string>();
        foreach (var ext in cert.Extensions)
        {
            if (ext.Oid?.Value != "2.5.29.17") continue;
            var reader = new AsnReader(ext.RawData, AsnEncodingRules.DER).ReadSequence();
            while (reader.HasData)
            {
                var tag = reader.PeekTag();
                if (tag.TagClass == TagClass.ContextSpecific && tag.TagValue == 6) uris.Add(Encoding.ASCII.GetString(reader.ReadOctetString(new Asn1Tag(TagClass.ContextSpecific, 6))));
                else if (tag.TagClass == TagClass.ContextSpecific && tag.TagValue == 2) dns.Add(Encoding.ASCII.GetString(reader.ReadOctetString(new Asn1Tag(TagClass.ContextSpecific, 2))));
                else reader.ReadEncodedValue();
            }
        }
        return (uris, dns);
    }

    /// <summary>Name.rfc4514_string(): RDNs in reverse order, comma separated.</summary>
    static string Rfc4514(X500DistinguishedName name)
    {
        var parts = new List<string>();
        foreach (var rdn in name.EnumerateRelativeDistinguishedNames())
        {
            var oid = rdn.GetSingleElementType().Value;
            var label = oid switch
            {
                "2.5.4.3" => "CN", "2.5.4.10" => "O", "2.5.4.11" => "OU", "2.5.4.6" => "C", "2.5.4.7" => "L", "2.5.4.8" => "ST",
                "0.9.2342.19200300.100.1.25" => "DC", "0.9.2342.19200300.100.1.1" => "UID", _ => oid,
            };
            var value = rdn.GetSingleElementValue() ?? "";
            var escaped = new StringBuilder();
            for (var i = 0; i < value.Length; i++)
            {
                var ch = value[i];
                if (ch is ',' or '+' or '"' or '\\' or '<' or '>' or ';' || (i == 0 && ch is '#' or ' ') || (i == value.Length - 1 && ch == ' ')) escaped.Append('\\');
                escaped.Append(ch);
            }
            parts.Add(label + "=" + escaped);
        }
        parts.Reverse();
        return string.Join(',', parts);
    }

    public static JsonObject Info(string path)
    {
        if (!File.Exists(path)) return new JsonObject { ["present"] = false, ["path"] = path };
        X509Certificate2 cert;
        try { cert = LoadAny(File.ReadAllBytes(path)); }
        catch (Exception exc) { return new JsonObject { ["present"] = false, ["path"] = path, ["error"] = "Certificato non valido: " + exc.Message }; }
        using (cert)
        {
            var (uris, dns) = SanValues(cert);
            return new JsonObject
            {
                ["present"] = true, ["path"] = path, ["application_uri"] = uris.FirstOrDefault() ?? "",
                ["uris"] = new JsonArray(uris.Select(u => (JsonNode?)u).ToArray()), ["dns_names"] = new JsonArray(dns.Select(u => (JsonNode?)u).ToArray()),
                ["subject"] = Rfc4514(cert.SubjectName), ["issuer"] = Rfc4514(cert.IssuerName),
                ["valid_from"] = Py.Iso(cert.NotBefore.ToUniversalTime()), ["valid_to"] = Py.Iso(cert.NotAfter.ToUniversalTime()),
                ["thumbprint_sha256"] = string.Join(':', SHA256.HashData(cert.RawData).Select(b => b.ToString("X2"))),
            };
        }
    }

    public JsonObject ClientInfo()
    {
        EnsureClient();
        var info = Info(paths.CertFile);
        info["key_path"] = paths.KeyFile;
        return info;
    }

    /// <summary>Restore saved certificate copies only when the active files are missing.</summary>
    public void RestoreSaved()
    {
        Directory.CreateDirectory(paths.CertDir);
        foreach (var (src, dst) in new[] { ("client.der", paths.CertFile), ("client-key.pem", paths.KeyFile), ("server.der", paths.ServerCertFile) })
        {
            var source = Path.Combine(paths.SettingsCertDir, src);
            if (File.Exists(source) && !File.Exists(dst)) File.Copy(source, dst);
        }
    }

    /// <summary>Preserve active user certificates; restore missing files from the supplied bundle.</summary>
    public void BootstrapBundle()
    {
        var seeds = Path.Combine(paths.BaseDir, "seed_certificates");
        var names = new[] { "client.der", "client-key.pem", "server.der" };
        if (!Directory.Exists(seeds) || !names.Any(n => File.Exists(Path.Combine(seeds, n)))) return;
        Directory.CreateDirectory(paths.CertDir);
        var bundle = Path.Combine(paths.CertDir, "embedded-v1.1.10");
        Directory.CreateDirectory(bundle);
        foreach (var (name, destination) in new[] { ("client.der", paths.CertFile), ("client-key.pem", paths.KeyFile), ("server.der", paths.ServerCertFile) })
        {
            var source = Path.Combine(seeds, name);
            if (!File.Exists(source)) throw new InvalidOperationException("Bundle certificati incompleto: " + name);
            if (!File.Exists(Path.Combine(bundle, name))) File.Copy(source, Path.Combine(bundle, name));
            if (!File.Exists(destination)) File.Copy(source, destination);
        }
        var trusted = Path.Combine(paths.CertDir, "trusted");
        Directory.CreateDirectory(trusted);
        File.Copy(paths.ServerCertFile, Path.Combine(trusted, "server.der"), true);
        if (Info(paths.CertFile)["application_uri"].Str() is { Length: > 0 } uri)
        {
            var cfg = config.Load();
            cfg["application_uri"] = uri;
            config.Save(cfg);
        }
    }
}
