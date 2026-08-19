import { useEffect, useState } from "react";
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
  // Which jobs have been told to stop, and when. The backend cannot tear a
  // job down mid-instruction, so between the click and the job actually
  // ending there is a wait - and a Cancel button that still looks clickable
  // during it reads as "nothing happened".
  const [stopping, setStopping] = useState<Record<number, number>>({});
  const [problem, setProblem] = useState("");
  const [, tick] = useState(0);

  // Re-render once a second so the "still stopping" note can appear.
  useEffect(() => {
    if (Object.keys(stopping).length === 0) return;
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [stopping]);

  if (jobs.length === 0) return null;

  const control = async (job: Job, action: "pause" | "resume" | "cancel") => {
    setProblem("");
    if (action === "cancel") setStopping((s) => ({ ...s, [job.id]: Date.now() }));
    try {
      await api.post(`/api/jobs/${job.id}/${action}`);
      onChange();
    } catch (err) {
      setProblem((err as Error).message);
      setStopping((s) => {
        const next = { ...s };
        delete next[job.id];
        return next;
      });
    }
  };

  return (
    <div className="space-y-2">
      {jobs.map((job) => {
        const since = stopping[job.id];
        const waiting = since ? Math.round((Date.now() - since) / 1000) : 0;
        return (
          <div key={job.id} className="card p-4">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-display text-white">{LABELS[job.type] ?? job.type}</span>
                  <Pill tone={since ? "amber" : job.status === "paused" ? "amber" : "magenta"}>
                    {since ? "stopping" : job.status}
                  </Pill>
                </div>
                <div className="mt-1 truncate text-sm text-slate-400">
                  {since
                    ? waiting < 4
                      ? "Stopping…"
                      : `Stopping — finishing the step already in flight (${waiting}s)`
                    : (job.message || "working…") +
                      (job.eta_sec ? ` · about ${formatDuration(job.eta_sec)} left` : "")}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <span className="font-mono text-sm text-slate-300">{job.progress.toFixed(0)}%</span>
                {job.status === "running" && !since && (
                  <button className="btn-ghost" onClick={() => control(job, "pause")}>
                    Pause
                  </button>
                )}
                {job.status === "paused" && !since && (
                  <button className="btn-amber" onClick={() => control(job, "resume")}>
                    Resume
                  </button>
                )}
                <button
                  className="btn-danger"
                  disabled={Boolean(since)}
                  onClick={() => control(job, "cancel")}
                >
                  {since ? "Stopping…" : "Cancel"}
                </button>
              </div>
            </div>
            <div className="mt-3">
              <Bar value={job.progress} />
            </div>
          </div>
        );
      })}
      {problem && <div className="text-sm text-danger">{problem}</div>}
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
