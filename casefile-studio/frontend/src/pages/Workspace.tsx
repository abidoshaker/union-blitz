import { useCallback, useEffect, useState } from "react";
import { JobBar, JobHistory } from "../components/JobBar";
import { Storyboard } from "../components/Storyboard";
import { Banner, Confirm, Empty, Modal, Pill, Stat } from "../components/ui";
import {
  api,
  type Json,
  formatBytes,
  formatDuration,
  formatRange,
  type Chapter,
  type Preflight,
  type Project,
} from "../lib/api";
import { useJobs } from "../lib/useJobs";

type Tab = "script" | "storyboard" | "render";

interface BatchBody {
  project_id: number;
  scene_ids: number[];
  op: string;
  payload: Record<string, unknown>;
}

interface BatchPreview {
  op: string;
  scene_count: number;
  affected: number;
  estimated_cost_usd: number;
  changes: Array<Record<string, unknown>>;
  truncated: boolean;
}

export function Workspace({ projectId, onBack }: { projectId: number; onBack: () => void }) {
  const [tab, setTab] = useState<Tab>("script");
  const [project, setProject] = useState<Project | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const { active, jobs, refresh } = useJobs(projectId);

  const load = useCallback(async () => {
    setProject(await api.get<Project>(`/api/projects/${projectId}`));
    setChapters(await api.get<Chapter[]>(`/api/projects/${projectId}/chapters`));
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  // A finishing job changes scene counts and chapter times, so re-read.
  useEffect(() => {
    if (active.length === 0) load();
  }, [active.length, load]);

  if (!project) return <Empty title="Loading project…" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-ghost" onClick={onBack}>
          ← Projects
        </button>
        <h2 className="text-xl">{project.title}</h2>
        <Pill tone={project.status === "rendered" ? "success" : "slate"}>{project.status}</Pill>
        <div className="ml-auto flex gap-1 rounded-xl bg-white/5 p-1">
          {(["script", "storyboard", "render"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-4 py-1.5 text-sm font-semibold capitalize transition-colors ${
                tab === t ? "bg-accent text-white" : "text-slate-300 hover:bg-white/5"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <JobBar jobs={active} onChange={refresh} />

      {tab === "script" && <ScriptTab project={project} onRan={refresh} onSaved={load} />}
      {tab === "storyboard" && (
        <StoryboardTab
          project={project}
          chapters={chapters}
          selected={selected}
          setSelected={setSelected}
          onRan={refresh}
          onChanged={load}
        />
      )}
      {tab === "render" && <RenderTab project={project} chapters={chapters} onRan={refresh} />}

      <JobHistory jobs={jobs} />
    </div>
  );
}

// ---------------------------------------------------------------------------

function ScriptTab({
  project,
  onRan,
  onSaved,
}: {
  project: Project;
  onRan: () => void;
  onSaved: () => void;
}) {
  const [text, setText] = useState("");
  const [stats, setStats] = useState({ word_count: 0, estimated_runtime_sec: 0 });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .get<{ raw_text: string; word_count: number; estimated_runtime_sec: number }>(
        `/api/projects/${project.id}/script`,
      )
      .then((s) => {
        setText(s.raw_text);
        setStats({ word_count: s.word_count, estimated_runtime_sec: s.estimated_runtime_sec });
      });
  }, [project.id]);

  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const runtime = (words / 150) * 60;

  const save = async () => {
    setSaving(true);
    try {
      const res = await api.post<{ word_count: number; estimated_runtime_sec: number }>(
        `/api/projects/${project.id}/script`,
        { raw_text: text },
      );
      setStats(res);
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const run = async (stage: string) => {
    await save();
    await api.post(`/api/projects/${project.id}/run/${stage}`, {});
    onRan();
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Words" value={words.toLocaleString()} hint="typed in the box below" />
        <Stat label="Estimated runtime" value={formatDuration(runtime)} hint="at 150 words per minute" />
        <Stat
          label="Estimated scenes"
          value={Math.max(1, Math.round(runtime / 15))}
          hint="roughly one scene every 15 seconds"
        />
      </div>

      <div className="card p-4">
        <label className="label">Narration script</label>
        <textarea
          className="input h-[46vh] resize-none font-mono text-sm leading-relaxed"
          placeholder="Paste your full script here. Hour-long scripts are expected — around 9,000 words."
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save script"}
          </button>
          <button className="btn-primary" onClick={() => run("segment")} disabled={!words}>
            Split into scenes
          </button>
          <button className="btn-amber ml-auto" onClick={() => run("build")} disabled={!words}>
            Build the whole video
          </button>
        </div>
      </div>

      <Banner tone="info" title="Your words stay your words">
        Scenes are cut from the script by character position, so the narration is your exact text.
        The model only decides where the cuts go and what each image should show — it never rewrites
        a sentence.
      </Banner>

      {stats.word_count > 0 && (
        <div className="text-sm text-slate-400">
          Saved: {stats.word_count.toLocaleString()} words ·{" "}
          {formatDuration(stats.estimated_runtime_sec)} estimated runtime
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function StoryboardTab({
  project,
  chapters,
  selected,
  setSelected,
  onRan,
  onChanged,
}: {
  project: Project;
  chapters: Chapter[];
  selected: Set<number>;
  setSelected: (s: Set<number>) => void;
  onRan: () => void;
  onChanged: () => void;
}) {
  const [preview, setPreview] = useState<(BatchPreview & { body: BatchBody }) | null>(null);

  const batch = async (op: string, payload: Record<string, unknown> = {}) => {
    const body: BatchBody = { project_id: project.id, scene_ids: [...selected], op, payload };
    const result = await api.post<BatchPreview>("/api/batch/preview", body);
    setPreview({ ...result, body });
  };

  const apply = async () => {
    if (!preview) return;
    await api.post("/api/batch/apply", preview.body);
    setPreview(null);
    setSelected(new Set());
    onRan();
    onChanged();
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Scenes" value={project.scene_count} />
        <Stat label="With audio" value={`${project.scenes_with_audio}/${project.scene_count}`} />
        <Stat label="With image" value={`${project.scenes_with_image}/${project.scene_count}`} />
        <Stat
          label="Flagged"
          value={project.flagged_real_person}
          hint="real people — archival photos only"
        />
      </div>

      <SourcingPanel project={project} onSaved={onChanged} />

      <div className="card flex flex-wrap items-center gap-2 p-3">
        <span className="text-sm text-slate-400">
          {selected.size > 0 ? `${selected.size} selected` : "Select scenes for a batch action"}
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={() => api.post(`/api/projects/${project.id}/run/narrate`, {}).then(onRan)}>
            Narrate all
          </button>
          <button className="btn-ghost" onClick={() => api.post(`/api/projects/${project.id}/run/images`, {}).then(onRan)}>
            Source all images
          </button>
          <button className="btn-ghost" disabled={!selected.size} onClick={() => batch("regenerate-audio")}>
            Re-record selected
          </button>
          <button className="btn-ghost" disabled={!selected.size} onClick={() => batch("swap-images")}>
            Swap images
          </button>
        </div>
      </div>

      {preview && (
        <div className="card p-4">
          <div className="flex items-center gap-3">
            <h3 className="text-base">Confirm batch</h3>
            <Pill tone="amber">{preview.op}</Pill>
            <div className="ml-auto flex gap-2">
              <button className="btn-ghost" onClick={() => setPreview(null)}>
                Cancel
              </button>
              <button className="btn-primary" onClick={apply}>
                {preview.estimated_cost_usd > 0
                  ? `Apply to ${preview.affected} scenes — $${preview.estimated_cost_usd.toFixed(2)}`
                  : `Apply to ${preview.affected} scenes`}
              </button>
            </div>
          </div>
          {preview.estimated_cost_usd > 0 && (
            <p className="mt-2 text-sm text-amber">
              This will spend about ${preview.estimated_cost_usd.toFixed(2)} with your narration provider.
            </p>
          )}
        </div>
      )}

      <Storyboard
        projectId={project.id}
        chapters={chapters}
        selected={selected}
        onSelect={setSelected}
        onSceneChanged={onChanged}
      />
    </div>
  );
}

function SourcingPanel({ project, onSaved }: { project: Project; onSaved: () => void }) {
  const [cfg, setCfg] = useState<Json>(project.settings ?? {});
  const [saving, setSaving] = useState(false);

  const set = (key: string, value: unknown) => setCfg((c) => ({ ...c, [key]: value }));

  const save = async () => {
    setSaving(true);
    try {
      await api.patch(`/api/projects/${project.id}`, { settings: cfg });
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <h3 className="text-base">Sourcing</h3>
        <span className="text-xs text-slate-500">
          what the app goes and finds for each scene
        </span>
        <button className="btn-amber ml-auto !py-1 !px-3 text-xs" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block">
          <span className="label">Motion footage</span>
          <select
            className="input"
            value={cfg.video_enabled ? "on" : "off"}
            onChange={(e) => set("video_enabled", e.target.value === "on")}
          >
            <option value="off">Stills only</option>
            <option value="on">Mix in video clips</option>
          </select>
        </label>

        <label className="block">
          <span className="label">Clip source</span>
          <select
            className="input"
            value={String(cfg.video_provider ?? "pexels_video")}
            onChange={(e) => set("video_provider", e.target.value)}
          >
            <option value="pexels_video">Pexels — B-roll</option>
            <option value="pixabay_video">Pixabay — B-roll</option>
            <option value="internet_archive">Internet Archive — archival</option>
          </select>
        </label>

        <label className="block">
          <span className="label">Share of scenes with video</span>
          <select
            className="input"
            value={String(cfg.video_share ?? 0.35)}
            onChange={(e) => set("video_share", Number(e.target.value))}
          >
            <option value="0.15">A little (15%)</option>
            <option value="0.35">Some (35%)</option>
            <option value="0.6">A lot (60%)</option>
          </select>
        </label>

        <label className="block">
          <span className="label">Blur faces</span>
          <select
            className="input"
            value={String(cfg.blur_faces ?? "real_person")}
            onChange={(e) => set("blur_faces", e.target.value)}
          >
            <option value="real_person">On flagged scenes</option>
            <option value="all">Everywhere</option>
            <option value="off">Off</option>
          </select>
        </label>

        <label className="block sm:col-span-2">
          <span className="label">When a clip has its own sound</span>
          <select
            className="input"
            value={String(cfg.clip_audio_default ?? "")}
            onChange={(e) => set("clip_audio_default", e.target.value)}
          >
            <option value="">Decide per source (archival speaks, B-roll sits under)</option>
            <option value="soundbite">Let it speak — pause the voiceover</option>
            <option value="ambient">Keep it under the voiceover, ducked</option>
            <option value="mute">Mute it — voiceover only</option>
          </select>
        </label>
      </div>

      <p className="mt-3 text-xs text-slate-500">
        Face blurring is an assist, not a guarantee — it misses faces in profile, in shadow and at
        low resolution. Check the preview render before you publish.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------

function RenderTab({
  project,
  chapters,
  onRan,
}: {
  project: Project;
  chapters: Chapter[];
  onRan: () => void;
}) {
  const [check, setCheck] = useState<Preflight | null>(null);
  const [renders, setRenders] = useState<any[]>([]);
  const [usage, setUsage] = useState<Json | null>(null);
  const [playing, setPlaying] = useState<any>(null);
  const [confirm, setConfirm] = useState<null | { kind: "render" | "clear" | "wipe"; id?: number }>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setCheck(await api.get<Preflight>(`/api/projects/${project.id}/preflight`));
    setRenders(await api.get<any[]>(`/api/projects/${project.id}/renders`));
    setUsage(await api.get<Json>(`/api/projects/${project.id}/usage`));
  }, [project.id]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-4">
      {check && (
        <>
          <div className="grid gap-3 sm:grid-cols-4">
            <Stat label="Video length" value={formatDuration(check.estimated_video_sec)} />
            <Stat
              label="Render time"
              value={formatRange(check.estimated_render_sec_low, check.estimated_render_sec_high)}
              hint={`${check.workers} cores`}
            />
            <Stat
              label="Scratch disk"
              value={formatBytes(check.estimated_disk_bytes)}
              hint={`${formatBytes(check.free_disk_bytes)} free`}
            />
            <Stat label="Narration cost" value={`$${check.narration_cost_usd.toFixed(2)}`} />
          </div>

          {check.problems.length > 0 && (
            <Banner tone="danger" title="Cannot render yet">
              {check.problems.join(" ")}
            </Banner>
          )}
        </>
      )}

      <Banner tone="amber" title="Render one chapter first">
        On an hour-long video a full render takes hours. A chapter preview takes minutes and shows
        you the voice, the caption style and the pacing while there is still time to change them.
      </Banner>

      <div className="card flex flex-wrap gap-2 p-4">
        <button
          className="btn-amber"
          disabled={chapters.length === 0}
          onClick={() =>
            api.post(`/api/projects/${project.id}/render/preview`, {}).then(() => {
              onRan();
              load();
            })
          }
        >
          Preview render · first chapter
        </button>
        <button
          className="btn-primary ml-auto"
          disabled={!check?.ok}
          onClick={() =>
            api.post(`/api/projects/${project.id}/run/render`, {}).then(() => {
              onRan();
              load();
            })
          }
        >
          Render the full video
        </button>
      </div>

      {renders.length === 0 ? (
        <Empty title="No renders yet">Your finished files will appear here.</Empty>
      ) : (
        <div className="card divide-y divide-white/5">
          {renders.map((r) => (
            <div key={r.id} className="flex flex-wrap items-center gap-3 p-4">
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-sm text-slate-200">{r.path}</div>
                <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-400">
                  <Pill>{r.variant}</Pill>
                  <span>{formatDuration(r.duration)}</span>
                  <span>{formatBytes(r.size_bytes)}</span>
                  {r.ad_breaks?.length > 0 && (
                    <span className="text-amber">{r.ad_breaks.length} mid-roll markers</span>
                  )}
                </div>
              </div>
              <button className="btn-amber !px-3 !py-1" onClick={() => setPlaying(r)}>
                ▶ Watch
              </button>
              <a className="btn-ghost" href={r.download_url} download>
                Download
              </a>
              <button
                className="btn-danger !px-3 !py-1"
                onClick={() => setConfirm({ kind: "render", id: r.id })}
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      )}

      {usage && (
        <div className="card p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h3 className="text-base">Disk</h3>
            <span className="font-mono text-sm text-slate-300">
              {formatBytes(Number(usage.total_bytes))}
            </span>
            <span className="text-xs text-slate-500">
              scene clips {formatBytes(Number(usage.clip_bytes))} · finished videos{" "}
              {formatBytes(Number(usage.render_bytes))} · downloads{" "}
              {formatBytes(Number(usage.source_bytes))} · previews{" "}
              {formatBytes(Number(usage.preview_bytes))}
            </span>
            <div className="ml-auto flex gap-2">
              <button className="btn-ghost" onClick={() => setConfirm({ kind: "clear" })}>
                Free up space
              </button>
              <button className="btn-danger" onClick={() => setConfirm({ kind: "wipe" })}>
                Clear everything
              </button>
            </div>
          </div>
          <p className="text-xs text-slate-500">
            <strong className="text-slate-400">Free up space</strong> deletes scene clips and
            previews only — they rebuild themselves on the next render, and your finished videos and
            downloaded footage are untouched.{" "}
            <strong className="text-slate-400">Clear everything</strong> also removes the finished
            videos and every downloaded image and clip, leaving the script and scene list.
          </p>
        </div>
      )}

      {playing && (
        <Modal title={`${playing.variant} · ${formatDuration(playing.duration)}`} onClose={() => setPlaying(null)} wide>
          <video
            src={playing.download_url}
            controls
            autoPlay
            className="w-full rounded-xl border border-white/10 bg-black"
          />
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
            <span>{formatBytes(playing.size_bytes)}</span>
            {playing.ad_breaks?.length > 0 && (
              <span className="text-amber">
                mid-roll markers at{" "}
                {playing.ad_breaks.map((b: number) => formatDuration(b)).join(", ")}
              </span>
            )}
          </div>
        </Modal>
      )}

      {confirm && (
        <Confirm
          title={
            confirm.kind === "render"
              ? "Delete this video?"
              : confirm.kind === "clear"
                ? "Free up space?"
                : "Clear everything?"
          }
          danger={confirm.kind !== "clear"}
          busy={busy}
          confirmLabel={
            confirm.kind === "render"
              ? "Delete the video"
              : confirm.kind === "clear"
                ? "Delete the scene clips"
                : "Delete videos and downloads"
          }
          body={
            confirm.kind === "render" ? (
              <>The MP4, its subtitles and its chapters file will be removed from your disk.</>
            ) : confirm.kind === "clear" ? (
              <>
                Removes scene clips and previews — about{" "}
                {formatBytes(Number(usage?.clip_bytes ?? 0) + Number(usage?.preview_bytes ?? 0))}.
                They rebuild on the next render. Nothing else is touched.
              </>
            ) : (
              <>
                Removes every finished video and every downloaded image and clip — about{" "}
                {formatBytes(Number(usage?.total_bytes ?? 0))}. Your script and scene list stay, but
                narration and pictures will have to be sourced again.
              </>
            )
          }
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            setBusy(true);
            try {
              if (confirm.kind === "render") {
                await api.del(`/api/renders/${confirm.id}`);
              } else {
                await api.post(`/api/projects/${project.id}/clear`, {
                  drop_renders: confirm.kind === "wipe",
                  drop_sources: confirm.kind === "wipe",
                });
              }
              await load();
              onRan();
            } finally {
              setBusy(false);
              setConfirm(null);
            }
          }}
        />
      )}

      {chapters.length > 0 && (
        <div className="card p-4">
          <div className="label">Chapters — paste into your YouTube description</div>
          <pre className="mt-1 overflow-x-auto rounded-xl bg-base/70 p-3 font-mono text-xs text-slate-300">
            {chapters
              .map((c, i) => `${i === 0 ? "00:00" : formatDuration(c.start_time)} ${c.title}`)
              .join("\n")}
          </pre>
        </div>
      )}
    </div>
  );
}
