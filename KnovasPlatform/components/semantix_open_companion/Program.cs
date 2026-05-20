using System.Diagnostics;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace SemantixOpenCompanion;

/// <summary>
/// Handles semantix-doc:open?token=...&apiBase=https%3A%2F%2Fhost%2F from the browser,
/// redeems the token at DocBridge /api/open-tokens/redeem, then ShellExecute on the returned UNC.
/// Does not download the file to %TEMP% for open (UNC-only contract).
/// </summary>
internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 0)
        {
            return 1;
        }

        var raw = string.Join(" ", args).Trim().Trim('"');
        if (!raw.StartsWith("semantix-doc:", StringComparison.OrdinalIgnoreCase))
        {
            MessageBox.Show("Unerwartetes Protokoll. Erwartet wird semantix-doc:…", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 2;
        }

        var q = raw.IndexOf('?', StringComparison.Ordinal);
        if (q < 0 || q >= raw.Length - 1)
        {
            MessageBox.Show("Keine Abfrageparameter (token, apiBase).", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 3;
        }

        var query = raw[(q + 1)..];
        var kv = ParseQuery(query);
        if (!kv.TryGetValue("token", out var token) || string.IsNullOrEmpty(token))
        {
            MessageBox.Show("Parameter token fehlt.", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 4;
        }

        if (!kv.TryGetValue("apiBase", out var apiBase) || string.IsNullOrEmpty(apiBase))
        {
            MessageBox.Show("Parameter apiBase fehlt.", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 5;
        }

        token = Uri.UnescapeDataString(token);
        apiBase = Uri.UnescapeDataString(apiBase).TrimEnd('/');

        if (!Uri.TryCreate(apiBase, UriKind.Absolute, out var baseUri) ||
            (baseUri.Scheme != Uri.UriSchemeHttp && baseUri.Scheme != Uri.UriSchemeHttps))
        {
            MessageBox.Show("apiBase muss eine http(s)-URL sein.", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 6;
        }

        string? unc = null;
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(60) };
            using var req = new HttpRequestMessage(HttpMethod.Post, $"{apiBase}/api/open-tokens/redeem");
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
            req.Content = new StringContent("{}", Encoding.UTF8, "application/json");

            using var resp = http.Send(req);
            var body = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
            using var doc = JsonDocument.Parse(body);
            var root = doc.RootElement;
            if (!root.TryGetProperty("success", out var ok) || !ok.GetBoolean())
            {
                var err = root.TryGetProperty("error", out var e) ? e.GetString() : body;
                MessageBox.Show($"Redeem fehlgeschlagen: {err}", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 7;
            }

            if (!root.TryGetProperty("unc", out var uncEl))
            {
                MessageBox.Show("Antwort ohne unc.", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 8;
            }

            unc = uncEl.GetString();
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Netzwerkfehler: {ex.Message}", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 9;
        }

        if (string.IsNullOrWhiteSpace(unc))
        {
            MessageBox.Show("Leerer UNC-Pfad.", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 10;
        }

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = unc,
                UseShellExecute = true,
            };
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            MessageBox.Show($"ShellExecute: {ex.Message}\n\nUNC: {unc}", "Semantix Open Companion", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 11;
        }

        return 0;
    }

    private static Dictionary<string, string> ParseQuery(string query)
    {
        var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var pair in query.Split('&', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var eq = pair.IndexOf('=');
            if (eq <= 0) continue;
            var k = pair[..eq];
            var v = pair[(eq + 1)..];
            d[k] = v;
        }
        return d;
    }
}
