import { api, formatDuration, type Job } from "../lib/api";
import { Bar, Pill } from "./ui";

const LABELS: Record<string, string> = {
  segment: "Splitting the script",
  narrate: "Recording narration",
  images: "Sourcing images",
  render: "Rendering video",
  build: "Building the whole video",
};

export function JobBar({ jobs, onChange }: { jobs: Job[]; onChange: () => void }) {
  if (jobs.length === 0) return null;

  return (
    <div className="space-y-2">
      {jobs.map((job) => (
        <div key={job.id} className="card p-4">
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-display text-white">{LABELS[job.type] ?? job.type}</span>
                <Pill tone={job.status === "paused" ? "amber" : "magenta"}>{job.status}</Pill>
              </div>
              <div className="mt-1 truncate text-sm text-slate-400">
                {job.message || "working…"}
                {job.eta_sec ? ` · about ${formatDuration(job.eta_sec)} left` : ""}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="font-mono text-sm text-slate-300">{job.progress.toFixed(0)}%</span>
              {job.status === "running" && (
                <button
                  className="btn-ghost"
                  onClick={async () => {
                    await api.post(`/api/jobs/${job.id}/pause`);
                    onChange();
                  }}
                >
                  Pause
                </button>
              )}
              {job.status === "paused" && (
                <button
                  className="btn-amber"
                  onClick={async () => {
                    await api.post(`/api/jobs/${job.id}/resume`);
                    onChange();
                  }}
                >
                  Resume
                </button>
              )}
              <button
                className="btn-danger"
                onClick={async () => {
                  await api.post(`/api/jobs/${job.id}/cancel`);
                  onChange();
                }}
              >
                Cancel
              </button>
            </div>
          </div>
          <div className="mt-3">
            <Bar value={job.progress} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function JobHistory({ jobs }: { jobs: Job[] }) {
  const finished = jobs.filter((j) => ["done", "error", "canceled"].includes(j.status)).slice(0, 8);
  if (finished.length === 0) return null;
  return (
    <div className="card divide-y divide-white/5">
      {finished.map((job) => (
        <div key={job.id} className="flex items-start justify-between gap-4 px-4 py-3">
          <div className="min-w-0">
            <div className="text-sm text-slate-200">{LABELS[job.type] ?? job.type}</div>
            {job.error && (
              <div className="mt-1 whitespace-pre-wrap text-xs text-danger">{job.error.split("\n")[0]}</div>
            )}
          </div>
          <Pill tone={job.status === "done" ? "success" : job.status === "error" ? "danger" : "slate"}>
            {job.status}
          </Pill>
        </div>
      ))}
    </div>
  );
}
