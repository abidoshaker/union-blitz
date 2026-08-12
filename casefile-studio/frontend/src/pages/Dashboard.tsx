import { useEffect, useState } from "react";
import { api, formatDuration, type Project } from "../lib/api";
import { Empty, Pill, ProgressRing } from "../components/ui";

export function Dashboard({ onOpen }: { onOpen: (id: number) => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => setProjects(await api.get<Project[]>("/api/projects"));
  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    if (!title.trim()) return;
    setBusy(true);
    try {
      const project = await api.post<Project>("/api/projects", { title });
      setTitle("");
      await load();
      onOpen(project.id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="card p-6">
        <h2 className="text-xl">Start a new case file</h2>
        <p className="mt-1 text-sm text-slate-400">
          One project per video. Paste an hour-long script and the app handles the rest.
        </p>
        <div className="mt-4 flex gap-2">
          <input
            className="input"
            placeholder="e.g. The Ledger on Mercer Street"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
          <button className="btn-primary shrink-0" onClick={create} disabled={busy || !title.trim()}>
            New project
          </button>
        </div>
      </div>

      {projects.length === 0 ? (
        <Empty title="No projects yet">Create one above to get started.</Empty>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {projects.map((p) => {
            const pct = p.scene_count ? (p.scenes_ready / p.scene_count) * 100 : 0;
            return (
              <button
                key={p.id}
                onClick={() => onOpen(p.id)}
                className="card p-5 text-left transition-all hover:border-magenta/40 hover:shadow-glow"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate font-display text-lg text-white">{p.title}</div>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      <Pill tone={p.status === "rendered" ? "success" : "slate"}>{p.status}</Pill>
                      {p.flagged_real_person > 0 && (
                        <Pill tone="danger">{p.flagged_real_person} real-person</Pill>
                      )}
                    </div>
                  </div>
                  <ProgressRing value={pct} />
                </div>

                <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
                  <div>
                    <dt className="text-[10px] uppercase tracking-wider text-slate-500">Words</dt>
                    <dd className="font-mono text-sm text-slate-200">{p.word_count.toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt className="text-[10px] uppercase tracking-wider text-slate-500">Runtime</dt>
                    <dd className="font-mono text-sm text-slate-200">
                      {formatDuration(p.narration_sec || p.estimated_runtime_sec)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[10px] uppercase tracking-wider text-slate-500">Scenes</dt>
                    <dd className="font-mono text-sm text-slate-200">{p.scene_count}</dd>
                  </div>
                </dl>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
