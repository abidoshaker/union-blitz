import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type Json,
  type Project,
  type VoiceCatalogue,
  type VoiceOption,
} from "../lib/api";
import { Banner, Modal, Pill, Spinner } from "./ui";

/**
 * Choosing the narrator, and how they read.
 *
 * The choice that matters most on a monetised channel is not which voice
 * sounds nicest - it is which voices you are allowed to publish. Edge sounds
 * good and cannot be monetised; Kokoro is free, offline and can. So licence
 * is shown as a badge on every voice rather than buried in a footnote.
 */
export function NarrationPanel({
  project,
  onSaved,
}: {
  project: Project;
  onSaved: () => void;
}) {
  const [cfg, setCfg] = useState<Json>(project.settings ?? {});
  const [catalogue, setCatalogue] = useState<VoiceCatalogue | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");
  const [cloning, setCloning] = useState(false);
  const [sample, setSample] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);
  const [auditioning, setAuditioning] = useState("");

  const provider = String(cfg.tts_provider ?? "kokoro");
  const voiceId = String(cfg.voice_id ?? "");
  const set = (key: string, value: unknown) => setCfg((c) => ({ ...c, [key]: value }));

  useEffect(() => {
    api
      .get<VoiceCatalogue>("/api/voices")
      .then(setCatalogue)
      .catch((err) => setNote((err as Error).message))
      .finally(() => setLoading(false));
  }, []);

  // Audition on a line from the actual script, not a stock sentence — a voice
  // that suits generic prose can still be wrong for your writing.
  useEffect(() => {
    api
      .get<{ raw_text: string }>(`/api/projects/${project.id}/script`)
      .then((s) => {
        const first = (s.raw_text || "").split(/(?<=[.!?])\s+/).find((l) => l.trim().length > 40);
        if (first) setSample(first.trim().slice(0, 240));
      })
      .catch(() => undefined);
  }, [project.id]);

  const providers = catalogue?.providers ?? [];
  const current = providers.find((p) => p.name === provider);
  const voices = useMemo(
    () => (catalogue?.voices ?? []).filter((v) => v.provider === provider),
    [catalogue, provider],
  );
  const blocked = catalogue?.unavailable.find((u) => u.provider === provider);

  const save = async () => {
    setSaving(true);
    setNote("");
    try {
      await api.patch(`/api/projects/${project.id}`, { settings: cfg });
      onSaved();
      setNote("Saved.");
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const audition = async (voice: VoiceOption) => {
    setAuditioning(voice.id);
    setNote("");
    try {
      const res = await fetch("/api/voices/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: voice.provider,
          voice_id: voice.id,
          text: sample || undefined,
          speed: Number(cfg.speech_rate ?? 1),
          spoken_numbers: cfg.spoken_numbers !== false,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
      const url = URL.createObjectURL(await res.blob());
      if (audioRef.current) {
        audioRef.current.src = url;
        await audioRef.current.play();
      }
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setAuditioning("");
    }
  };

  return (
    <div className="card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-[15px]">Narration</h3>
        <span className="text-xs text-slate-500">who reads it, and how</span>
        <button
          className="btn-amber ml-auto !py-1 !px-3 text-xs"
          onClick={save}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      {loading && <Spinner label="Looking for voices…" />}

      {/* --- provider ------------------------------------------------- */}
      <div className="flex flex-wrap gap-2">
        {providers
          .filter((p) => p.name !== "draft" || provider === "draft")
          .map((p) => (
            <button
              key={p.name}
              className={p.name === provider ? "btn-amber" : "btn-ghost"}
              onClick={() => {
                set("tts_provider", p.name);
                set("voice_id", "");
              }}
              title={p.available ? p.label : p.unavailable_reason}
            >
              {p.label}
              {!p.available && <span className="ml-1 text-[10px] text-slate-500">· needs setup</span>}
            </button>
          ))}
      </div>

      {current && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <Pill tone={current.commercial_ok ? "success" : "danger"}>
            {current.commercial_ok ? "safe to monetise" : "drafting only"}
          </Pill>
          {current.is_local && <Pill tone="info">runs offline</Pill>}
          {current.supports_cloning && <Pill tone="magenta">can clone your voice</Pill>}
          <span className="text-slate-500">
            {current.cost_per_million_bytes > 0
              ? `$${current.cost_per_million_bytes.toFixed(0)} per million characters — about $${(
                  (project.word_count * 5.5) /
                  1_000_000 *
                  current.cost_per_million_bytes
                ).toFixed(2)} for this script`
              : "free"}
          </span>
        </div>
      )}

      {!current?.commercial_ok && provider !== "draft" && (
        <Banner tone="danger" title="Not for a monetised final">
          Fine for hearing the whole thing through before you pay for it. Switch to Fish Audio or
          Kokoro before the render you publish.
        </Banner>
      )}

      {blocked && (
        <Banner tone="amber" title="This provider is not ready">
          {blocked.reason}
        </Banner>
      )}

      {/* --- voices ---------------------------------------------------- */}
      {voices.length > 0 && (
        <div className="mt-3 max-h-72 space-y-1 overflow-y-auto pr-1">
          {voices.map((v) => (
            <div
              key={`${v.provider}:${v.id}`}
              className={`flex flex-wrap items-center gap-2 rounded-xl border p-2 transition-colors ${
                v.id === voiceId
                  ? "border-amber/60 bg-amber/5"
                  : "border-white/5 hover:border-white/15"
              }`}
            >
              <button
                className="min-w-0 flex-1 text-left"
                onClick={() => set("voice_id", v.id)}
              >
                <div className="truncate text-sm text-slate-200">{v.title}</div>
                <div className="truncate text-[11px] text-slate-500">
                  {v.note}
                  {v.tags.length > 0 && ` · ${v.tags.join(", ")}`}
                </div>
              </button>
              {v.is_clone && <Pill tone="magenta">yours</Pill>}
              {!v.commercial_ok && <Pill tone="danger">draft</Pill>}
              {v.id === voiceId && <Pill tone="success">narrator</Pill>}
              <button
                className="btn-ghost !px-2.5 !py-1 text-xs"
                onClick={() => audition(v)}
                disabled={auditioning === v.id}
              >
                {auditioning === v.id ? "…" : "▶ Hear it"}
              </button>
            </div>
          ))}
        </div>
      )}

      {sample && voices.length > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          Auditioning on your own first line, through the same rewrite and trim the real narration
          gets — so what you hear is what you get.
        </p>
      )}

      {/* --- delivery -------------------------------------------------- */}
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block">
          <span className="label">Pace</span>
          <select
            className="input"
            value={String(cfg.speech_rate ?? 1)}
            onChange={(e) => set("speech_rate", Number(e.target.value))}
          >
            <option value="0.85">Unhurried (0.85×)</option>
            <option value="0.92">Considered (0.92×)</option>
            <option value="1">Natural (1×)</option>
            <option value="1.08">Brisk (1.08×)</option>
            <option value="1.15">Urgent (1.15×)</option>
          </select>
        </label>

        <label className="block">
          <span className="label">Pauses between scenes</span>
          <select
            className="input"
            value={String(cfg.pause_scale ?? 1)}
            onChange={(e) => set("pause_scale", Number(e.target.value))}
          >
            <option value="0">None — run it together</option>
            <option value="0.6">Tight</option>
            <option value="1">Natural</option>
            <option value="1.4">Room to breathe</option>
            <option value="1.8">Slow and heavy</option>
          </select>
        </label>

        <label className="block">
          <span className="label">Numbers and initials</span>
          <select
            className="input"
            value={cfg.spoken_numbers === false ? "off" : "on"}
            onChange={(e) => set("spoken_numbers", e.target.value === "on")}
          >
            <option value="on">Say them aloud</option>
            <option value="off">Send as written</option>
          </select>
          <span className="mt-1 block text-[11px] text-slate-500">
            1991 → “nineteen ninety-one”, DEA → “D-E-A”
          </span>
        </label>

        <label className="block">
          <span className="label">Spend ceiling</span>
          <input
            className="input"
            type="number"
            min={0}
            step={1}
            placeholder="no limit"
            value={cfg.spend_ceiling == null ? "" : String(cfg.spend_ceiling)}
            onChange={(e) =>
              set("spend_ceiling", e.target.value === "" ? null : Number(e.target.value))
            }
          />
        </label>
      </div>

      <p className="mt-3 text-xs text-slate-500">
        Each scene is recorded separately, so the rests between them are what make an hour sound
        like a person reading rather than a list. Pauses lengthen at a full stop, longer at a
        question, longest where a chapter turns over.
      </p>

      {current?.supports_cloning && (
        <button className="btn-ghost mt-3" onClick={() => setCloning(true)}>
          Clone your own voice
        </button>
      )}

      {note && <div className="mt-2 text-sm text-amber">{note}</div>}
      <audio ref={audioRef} className="hidden" />

      {cloning && (
        <CloneVoice
          provider={provider}
          onClose={() => setCloning(false)}
          onCloned={(id) => {
            set("voice_id", id);
            setCloning(false);
            api.get<VoiceCatalogue>("/api/voices").then(setCatalogue);
          }}
        />
      )}
    </div>
  );
}

/** Upload a reference clip and get back a voice that sounds like it. */
function CloneVoice({
  provider,
  onClose,
  onCloned,
}: {
  provider: string;
  onClose: () => void;
  onCloned: (voiceId: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [transcript, setTranscript] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const submit = async () => {
    if (!file || !title.trim()) return;
    setBusy(true);
    setNote("");
    try {
      const form = new FormData();
      form.append("title", title.trim());
      form.append("provider", provider);
      form.append("reference_text", transcript);
      form.append("sample", file);
      const res = await fetch("/api/voices/clone", { method: "POST", body: form });
      if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
      const body = await res.json();
      onCloned(body.voice_id);
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Clone a voice" onClose={onClose}>
      <div className="space-y-3">
        <Banner tone="amber" title="Only a voice you have the right to use">
          Your own voice, or one you have written permission to clone. A narrator's voice is theirs,
          and cloning it without consent is the kind of thing that ends a channel.
        </Banner>

        <label className="block">
          <span className="label">Name it</span>
          <input
            className="input"
            placeholder="e.g. My narrator"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>

        <label className="block">
          <span className="label">Reference clip</span>
          <input
            className="input"
            type="file"
            accept="audio/*"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <span className="mt-1 block text-xs text-slate-500">
            Ten to fifteen seconds of clean speech, no music, no room echo.
          </span>
        </label>

        <label className="block">
          <span className="label">What the clip says (optional, improves the match)</span>
          <textarea
            className="input h-20 resize-none text-sm"
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
          />
        </label>

        {note && <div className="text-sm text-amber">{note}</div>}
        {busy && <Spinner label="Training the voice…" />}

        <button className="btn-primary" onClick={submit} disabled={busy || !file || !title.trim()}>
          Clone it
        </button>
      </div>
    </Modal>
  );
}
