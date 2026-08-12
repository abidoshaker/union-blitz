import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Banner, Pill } from "../components/ui";

interface ProviderInfo {
  provider: string;
  label: string;
  what: string;
  cost: string;
  url: string;
  configured: boolean;
}

interface Health {
  ffmpeg: { found: boolean; path: string; libx264: boolean; libass: boolean; problems: string[]; hw_encoders: string[] };
  disk_free_gb: number;
  disk_warning: boolean;
  cores: number;
  align_method: string;
  kokoro: { available: boolean; reason: string };
}

export function SettingsPanel() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, { ok: boolean; message: string }>>({});

  const load = async () => {
    const settings = await api.get<{ providers: ProviderInfo[] }>("/api/settings");
    setProviders(settings.providers);
    setHealth(await api.get<Health>("/api/settings/health"));
  };
  useEffect(() => {
    load();
  }, []);

  const save = async (provider: string) => {
    await api.post("/api/settings/keys", { provider, value: values[provider] ?? "" });
    setValues((v) => ({ ...v, [provider]: "" }));
    await load();
  };

  const test = async (provider: string) => {
    setResults((r) => ({ ...r, [provider]: { ok: false, message: "testing…" } }));
    const res = await api.post<{ ok: boolean; message: string }>(`/api/settings/keys/${provider}/test`);
    setResults((r) => ({ ...r, [provider]: res }));
  };

  return (
    <div className="space-y-6">
      {health && (
        <div className="space-y-3">
          {health.ffmpeg.problems.length > 0 ? (
            <Banner tone="danger" title="FFmpeg needs attention">
              {health.ffmpeg.problems.join(" ")} Run <code className="font-mono">install-deps.bat</code>, then{" "}
              <code className="font-mono">doctor.bat</code>.
            </Banner>
          ) : (
            <Banner tone="success" title="FFmpeg is ready">
              libx264 and libass are both present, so video and burned-in subtitles will work.
              {health.ffmpeg.hw_encoders.length > 0 &&
                ` Hardware encoders available: ${health.ffmpeg.hw_encoders.join(", ")}.`}
            </Banner>
          )}

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="card p-4">
              <div className="label">Render workers</div>
              <div className="font-display text-2xl text-white">{health.cores}</div>
              <div className="mt-1 text-xs text-slate-400">One core is left free so the PC stays usable.</div>
            </div>
            <div className="card p-4">
              <div className="label">Free disk</div>
              <div className={`font-display text-2xl ${health.disk_warning ? "text-danger" : "text-white"}`}>
                {health.disk_free_gb} GB
              </div>
              <div className="mt-1 text-xs text-slate-400">An hour-long render needs 4–8 GB of scratch.</div>
            </div>
            <div className="card p-4">
              <div className="label">Caption timing</div>
              <div className="font-display text-2xl text-white">{health.align_method}</div>
              <div className="mt-1 text-xs text-slate-400">
                {health.align_method === "proportional"
                  ? "Draft quality. Install the align tier for tighter timing."
                  : "Word-level timing available."}
              </div>
            </div>
          </div>

          {!health.kokoro.available && (
            <Banner tone="amber" title="Kokoro local voice is not ready">
              {health.kokoro.reason} Fish Audio and Edge TTS still work without it.
            </Banner>
          )}
        </div>
      )}

      <div className="card divide-y divide-white/5">
        {providers.map((p) => (
          <div key={p.provider} className="p-5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-display text-white">{p.label}</span>
              <Pill tone={p.configured ? "success" : "slate"}>{p.configured ? "key saved" : "no key"}</Pill>
              <a
                href={p.url}
                target="_blank"
                rel="noreferrer"
                className="ml-auto text-xs text-info hover:underline"
              >
                get a key →
              </a>
            </div>
            <p className="mt-1 text-sm text-slate-400">
              {p.what} · <span className="text-slate-300">{p.cost}</span>
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <input
                className="input flex-1 min-w-[220px] font-mono"
                type="password"
                placeholder={p.configured ? "saved — paste a new key to replace it" : "paste key here"}
                value={values[p.provider] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [p.provider]: e.target.value }))}
              />
              <button className="btn-primary" onClick={() => save(p.provider)} disabled={!values[p.provider]}>
                Save
              </button>
              <button className="btn-ghost" onClick={() => test(p.provider)} disabled={!p.configured}>
                Test
              </button>
            </div>
            {results[p.provider] && (
              <div
                className={`mt-2 text-sm ${results[p.provider].ok ? "text-success" : "text-danger"}`}
              >
                {results[p.provider].message}
              </div>
            )}
          </div>
        ))}
      </div>

      <Banner tone="info" title="Where keys are stored">
        Encrypted on this machine with a key held in the Windows Credential Manager. They are never
        written to the project folder and never sent anywhere except the provider they belong to.
      </Banner>
    </div>
  );
}
