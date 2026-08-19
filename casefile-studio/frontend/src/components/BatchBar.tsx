import { useEffect, useRef, useState } from "react";
import { api, type Project, type VoiceCatalogue } from "../lib/api";
import { SourcePicker, useProviders } from "./SourcePicker";
import { Pill } from "./ui";

export interface BatchBody {
  project_id: number;
  scene_ids: number[];
  op: string;
  payload: Record<string, unknown>;
}

export interface BatchPreview {
  op: string;
  scene_count: number;
  affected: number;
  estimated_cost_usd: number;
  changes: Array<Record<string, unknown>>;
  truncated: boolean;
}

/**
 * Do one thing to several scenes, having chosen exactly what.
 *
 * The point of selecting five scenes is usually that those five need something
 * specific — the archives rather than Pexels, or a different narrator for a
 * run of quoted testimony. A batch button with no options can only apply the
 * project defaults, which is the thing you were trying to get away from.
 */
export function BatchBar({
  project,
  selected,
  onRan,
  onChanged,
}: {
  project: Project;
  selected: Set<number>;
  onRan: () => void;
  onChanged: () => void;
}) {
  const providers = useProviders();
  const [panel, setPanel] = useState<"" | "images" | "voice">("");
  const [preview, setPreview] = useState<(BatchPreview & { body: BatchBody }) | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  // Batch choices start from the project's own settings, so "apply to these
  // five" without touching anything means what you would expect. Re-seeded
  // when the project's own sources change, or the panel keeps showing the
  // selection from before you edited them a moment ago.
  const projectSources = (project.settings?.image_sources as string[]) ?? [];
  const [sources, setSources] = useState<string[]>(projectSources);
  const seeded = useRef(JSON.stringify(projectSources));
  useEffect(() => {
    const now = JSON.stringify(projectSources);
    if (now !== seeded.current) {
      seeded.current = now;
      setSources(projectSources);
    }
  }, [projectSources]);
  const [replace, setReplace] = useState(true);
  const [voices, setVoices] = useState<VoiceCatalogue | null>(null);
  const [ttsProvider, setTtsProvider] = useState(String(project.settings?.tts_provider ?? ""));
  const [voiceId, setVoiceId] = useState(String(project.settings?.voice_id ?? ""));

  useEffect(() => {
    if (panel === "voice" && !voices) {
      api.get<VoiceCatalogue>("/api/voices").then(setVoices).catch(() => undefined);
    }
  }, [panel, voices]);

  const count = selected.size;

  // A run that is refused - no chapters yet, no key for that provider - has to
  // say so. Firing and forgetting reads as "the button does nothing".
  const start = async (stage: string) => {
    setNote("");
    try {
      await api.post(`/api/projects/${project.id}/run/${stage}`, {});
      onRan();
    } catch (err) {
      setNote((err as Error).message);
    }
  };

  const propose = async (op: string, payload: Record<string, unknown> = {}) => {
    setNote("");
    const body: BatchBody = { project_id: project.id, scene_ids: [...selected], op, payload };
    try {
      const result = await api.post<BatchPreview>("/api/batch/preview", body);
      setPreview({ ...result, body });
    } catch (err) {
      setNote((err as Error).message);
    }
  };

  const apply = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      await api.post("/api/batch/apply", preview.body);
      setPreview(null);
      setPanel("");
      onRan();
      onChanged();
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const voiceOptions = (voices?.voices ?? []).filter((v) => v.provider === ttsProvider);

  return (
    <div className="card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-400">
          {count > 0 ? (
            <>
              <span className="font-semibold text-amber">{count}</span> selected
            </>
          ) : (
            "Select scenes to act on just those"
          )}
        </span>

        <div className="ml-auto flex flex-wrap gap-2">
          <button
            className="btn-ghost"
            onClick={() => start("narrate")}
          >
            Narrate all
          </button>
          <button
            className="btn-ghost"
            onClick={() => start("images")}
          >
            Source all images
          </button>
          <button
            className={panel === "images" ? "btn-amber" : "btn-ghost"}
            disabled={!count}
            onClick={() => setPanel(panel === "images" ? "" : "images")}
          >
            Pictures for these {count || ""}
          </button>
          <button
            className={panel === "voice" ? "btn-amber" : "btn-ghost"}
            disabled={!count}
            onClick={() => setPanel(panel === "voice" ? "" : "voice")}
          >
            Voice for these {count || ""}
          </button>
        </div>
      </div>

      {panel === "images" && (
        <div className="mt-3 space-y-3 border-t border-white/5 pt-3">
          <SourcePicker
            label="Where to look for these scenes"
            hint="just this batch — the project's own sources are unchanged"
            emptyNote="Using the project's sources."
            providers={providers?.images ?? []}
            selected={sources}
            onChange={setSources}
            showAi
          />
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-slate-300">
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => setReplace(e.target.checked)}
              />
              Replace pictures these scenes already have
            </label>
            <button
              className="btn-primary ml-auto !py-1.5 !px-4 text-sm"
              onClick={() =>
                propose(replace ? "swap-images" : "source-images", {
                  ...(sources.length ? { image_sources: sources } : {}),
                })
              }
            >
              {replace ? `Re-source ${count} scenes` : `Fill in the empty ones`}
            </button>
          </div>
          {!replace && (
            <p className="text-[11px] text-slate-500">
              Leaves anything you already chose by hand exactly as it is.
            </p>
          )}
        </div>
      )}

      {panel === "voice" && (
        <div className="mt-3 space-y-3 border-t border-white/5 pt-3">
          <div>
            <span className="label">Narrator for these scenes</span>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {(voices?.providers ?? [])
                .filter((p) => p.name !== "draft")
                .map((p) => (
                  <button
                    key={p.name}
                    className={p.name === ttsProvider ? "btn-amber !py-1 !px-2.5 text-xs" : "btn-ghost !py-1 !px-2.5 text-xs"}
                    disabled={!p.available}
                    title={p.available ? p.label : p.unavailable_reason}
                    onClick={() => {
                      setTtsProvider(p.name);
                      setVoiceId("");
                    }}
                  >
                    {p.label}
                    {!p.commercial_ok && <span className="ml-1 text-[9px] text-danger">draft</span>}
                  </button>
                ))}
            </div>
          </div>

          {voiceOptions.length > 0 && (
            <select
              className="input"
              value={voiceId}
              onChange={(e) => setVoiceId(e.target.value)}
            >
              <option value="">Use the project's voice</option>
              {voiceOptions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.title}
                  {v.tags.length ? ` — ${v.tags.join(", ")}` : ""}
                </option>
              ))}
            </select>
          )}

          <button
            className="btn-primary !py-1.5 !px-4 text-sm"
            onClick={() =>
              propose("regenerate-audio", {
                ...(ttsProvider ? { tts_provider: ttsProvider } : {}),
                ...(voiceId ? { voice_id: voiceId } : {}),
              })
            }
          >
            Re-record {count} scenes
          </button>
          <p className="text-[11px] text-slate-500">
            Only these scenes change. Pace and pauses stay the project's, so a retake still sits
            inside the rest of the narration.
          </p>
        </div>
      )}

      {note && <div className="mt-2 text-sm text-danger">{note}</div>}

      {preview && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-amber/30 bg-amber/5 p-3">
          <Pill tone="amber">{preview.op}</Pill>
          <span className="text-sm text-slate-300">
            {preview.affected} of {preview.scene_count} selected scenes
            {preview.estimated_cost_usd > 0 &&
              ` · about $${preview.estimated_cost_usd.toFixed(2)}`}
          </span>
          <div className="ml-auto flex gap-2">
            <button className="btn-ghost !py-1 !px-3 text-sm" onClick={() => setPreview(null)}>
              Cancel
            </button>
            <button
              className="btn-primary !py-1 !px-3 text-sm"
              onClick={apply}
              disabled={busy || preview.affected === 0}
            >
              {busy ? "Working…" : "Do it"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
