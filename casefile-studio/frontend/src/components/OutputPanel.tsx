import { useEffect, useState } from "react";
import { api, type Chapter, type Json, type Project } from "../lib/api";

interface SettingsPayload {
  align_methods: string[];
  defaults: Record<string, number | string>;
}

/**
 * Everything about the finished file: frame, captions, sound, timing.
 *
 * All of it was already honoured by the render - it just had no control, so a
 * project was stuck on 1080p/30 with amber karaoke whether or not that was
 * what you wanted. Nothing here is new engine behaviour; it is the engine
 * becoming reachable.
 */
export function OutputPanel({
  project,
  chapters,
  onSaved,
}: {
  project: Project;
  chapters: Chapter[];
  onSaved: () => void;
}) {
  const [cfg, setCfg] = useState<Json>(project.settings ?? {});
  const [meta, setMeta] = useState<SettingsPayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);

  const set = (key: string, value: unknown) => setCfg((c) => ({ ...c, [key]: value }));

  useEffect(() => {
    api.get<SettingsPayload>("/api/settings").then(setMeta).catch(() => undefined);
  }, []);

  const save = async () => {
    setSaving(true);
    setNote("");
    try {
      await api.patch(`/api/projects/${project.id}`, { settings: cfg });
      onSaved();
      setNote("Saved. Already-rendered clips are reused where the change does not affect them.");
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const shape = `${cfg.width ?? 1920}x${cfg.height ?? 1080}`;

  return (
    <div className="card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-[15px]">Output</h3>
        <span className="text-xs text-slate-500">the finished file</span>
        <button className="btn-ghost ml-auto !py-1 !px-3 text-xs" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"} settings
        </button>
        {open && (
          <button className="btn-amber !py-1 !px-3 text-xs" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        )}
      </div>

      {!open ? (
        <p className="text-xs text-slate-500">
          {shape} · {String(cfg.fps ?? 30)} fps · {String(cfg.subtitle_style ?? "karaoke_amber").replace("_", " ")}{" "}
          captions · {String(cfg.loudness_lufs ?? -14)} LUFS
        </p>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block">
              <span className="label">Frame</span>
              <select
                className="input"
                value={shape}
                onChange={(e) => {
                  const [w, h] = e.target.value.split("x").map(Number);
                  set("width", w);
                  set("height", h);
                }}
              >
                <option value="1920x1080">1080p — YouTube</option>
                <option value="2560x1440">1440p — YouTube</option>
                <option value="3840x2160">2160p — 4K</option>
                <option value="1280x720">720p — small and fast</option>
                <option value="1080x1920">1080×1920 — Shorts, vertical</option>
              </select>
            </label>

            <label className="block">
              <span className="label">Frame rate</span>
              <select
                className="input"
                value={String(cfg.fps ?? 30)}
                onChange={(e) => set("fps", Number(e.target.value))}
              >
                <option value="24">24 — filmic</option>
                <option value="25">25 — PAL</option>
                <option value="30">30 — standard</option>
                <option value="60">60 — smooth, twice the render</option>
              </select>
            </label>

            <label className="block">
              <span className="label">Encoder</span>
              <select
                className="input"
                value={String(cfg.encoder ?? "libx264")}
                onChange={(e) => set("encoder", e.target.value)}
              >
                <option value="libx264">libx264 — CPU, most compatible</option>
                <option value="h264_nvenc">NVIDIA NVENC — much faster</option>
                <option value="h264_qsv">Intel QuickSync</option>
                <option value="h264_amf">AMD AMF</option>
              </select>
            </label>

            <label className="block">
              <span className="label">Quality vs speed</span>
              <select
                className="input"
                value={String(cfg.preset ?? "medium")}
                onChange={(e) => set("preset", e.target.value)}
              >
                <option value="ultrafast">Ultrafast — drafts</option>
                <option value="veryfast">Very fast</option>
                <option value="medium">Medium — the sensible default</option>
                <option value="slow">Slow — smaller file, longer wait</option>
              </select>
            </label>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block">
              <span className="label">Captions</span>
              <select
                className="input"
                value={cfg.burn_subtitles === false ? "off" : String(cfg.subtitle_style ?? "karaoke_amber")}
                onChange={(e) => {
                  if (e.target.value === "off") {
                    set("burn_subtitles", false);
                  } else {
                    set("burn_subtitles", true);
                    set("subtitle_style", e.target.value);
                  }
                }}
              >
                <option value="karaoke_amber">Karaoke — amber</option>
                <option value="karaoke_cyan">Karaoke — cyan</option>
                <option value="plain_white">Plain white</option>
                <option value="off">No burned captions</option>
              </select>
            </label>

            <label className="block">
              <span className="label">Word highlighting</span>
              <select
                className="input"
                value={cfg.karaoke === false ? "off" : "on"}
                onChange={(e) => set("karaoke", e.target.value === "on")}
              >
                <option value="on">Light each word as it is said</option>
                <option value="off">Whole line at once</option>
              </select>
            </label>

            <label className="block">
              <span className="label">Caption timing</span>
              <select
                className="input"
                value={String(cfg.align_method ?? "auto")}
                onChange={(e) => set("align_method", e.target.value)}
              >
                <option value="auto">Best available</option>
                {(meta?.align_methods ?? []).map((m) => (
                  <option key={m} value={m}>
                    {m === "proportional" ? "By character count (free, rough)" : m}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="label">Ken Burns</span>
              <select
                className="input"
                value={String(cfg.kenburns ?? "auto")}
                onChange={(e) => set("kenburns", e.target.value)}
              >
                <option value="auto">Vary it per scene</option>
                <option value="in">Always push in</option>
                <option value="out">Always pull out</option>
                <option value="none">Hold still</option>
              </select>
            </label>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block">
              <span className="label">Crossfade</span>
              <select
                className="input"
                value={String(cfg.transition_sec ?? 0.5)}
                onChange={(e) => set("transition_sec", Number(e.target.value))}
              >
                <option value="0">Hard cut</option>
                <option value="0.3">0.3 s</option>
                <option value="0.5">0.5 s</option>
                <option value="0.8">0.8 s — languid</option>
              </select>
            </label>

            <label className="block">
              <span className="label">Loudness target</span>
              <select
                className="input"
                value={String(cfg.loudness_lufs ?? -14)}
                onChange={(e) => set("loudness_lufs", Number(e.target.value))}
              >
                <option value="-14">−14 LUFS — YouTube</option>
                <option value="-16">−16 LUFS — podcast</option>
                <option value="-19">−19 LUFS — broadcast</option>
              </select>
            </label>

            <label className="block sm:col-span-2">
              <span className="label">Music bed (a file on this machine)</span>
              <input
                className="input font-mono text-xs"
                placeholder="C:\music\underscore.mp3 — ducked under the voice"
                value={String(cfg.music_path ?? "")}
                onChange={(e) => set("music_path", e.target.value)}
              />
            </label>
          </div>

          <label className="block">
            <span className="label">Disclaimer overlay on AI pictures</span>
            <input
              className="input"
              placeholder="e.g. Dramatisation — AI-generated image"
              value={String(cfg.disclaimer_text ?? "")}
              onChange={(e) => set("disclaimer_text", e.target.value)}
            />
          </label>

          {chapters.length > 0 && <ChapterTitles project={project} chapters={chapters} onSaved={onSaved} />}

          {note && <div className="text-sm text-amber">{note}</div>}
        </div>
      )}
    </div>
  );
}

/** Chapter titles become the YouTube chapter markers, so they are worth writing. */
function ChapterTitles({
  project,
  chapters,
  onSaved,
}: {
  project: Project;
  chapters: Chapter[];
  onSaved: () => void;
}) {
  const [titles, setTitles] = useState<Record<number, string>>({});
  const [savingId, setSavingId] = useState<number | null>(null);

  useEffect(() => {
    setTitles(Object.fromEntries(chapters.map((c) => [c.id, c.title])));
  }, [chapters]);

  const save = async (id: number) => {
    setSavingId(id);
    try {
      await api.patch(`/api/projects/${project.id}/chapters/${id}`, { title: titles[id] ?? "" });
      onSaved();
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div>
      <span className="label">Chapter titles</span>
      <div className="mt-1 space-y-1">
        {chapters.map((c) => (
          <div key={c.id} className="flex items-center gap-2">
            <span className="w-8 shrink-0 text-xs text-slate-500">{c.order_index + 1}</span>
            <input
              className="input"
              value={titles[c.id] ?? ""}
              onChange={(e) => setTitles((t) => ({ ...t, [c.id]: e.target.value }))}
              onBlur={() => save(c.id)}
              onKeyDown={(e) => e.key === "Enter" && save(c.id)}
            />
            <span className="w-24 shrink-0 text-right text-xs text-slate-500">
              {c.scene_count} scenes{savingId === c.id ? " · saving…" : ""}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-1 text-xs text-slate-500">
        These become the chapter markers in the description, so write them as a viewer would scan
        them. Saved when you leave the box.
      </p>
    </div>
  );
}
