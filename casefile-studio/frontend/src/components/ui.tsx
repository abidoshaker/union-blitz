import type { ReactNode } from "react";

export function Pill({
  tone = "slate",
  children,
}: {
  tone?: "slate" | "amber" | "success" | "danger" | "info" | "magenta";
  children: ReactNode;
}) {
  const tones = {
    slate: "bg-white/8 text-slate-300",
    amber: "bg-amber/15 text-amber",
    success: "bg-success/15 text-success",
    danger: "bg-danger/15 text-danger",
    info: "bg-info/15 text-info",
    magenta: "bg-magenta/15 text-magenta",
  } as const;
  return <span className={`pill ${tones[tone]}`}>{children}</span>;
}

export function ProgressRing({ value, size = 44 }: { value: number; size?: number }) {
  const r = size / 2 - 4;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={4} className="stroke-white/10" fill="none" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        strokeWidth={4}
        fill="none"
        stroke="url(#ringGradient)"
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={c - (Math.min(100, Math.max(0, value)) / 100) * c}
        className="transition-all duration-500"
      />
      <defs>
        <linearGradient id="ringGradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#FF2D75" />
          <stop offset="100%" stopColor="#7C3AED" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export function Bar({ value }: { value: number }) {
  return (
    <div className="h-2 w-full rounded-full bg-white/8 overflow-hidden">
      <div
        className="h-full bg-accent transition-all duration-500"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="card p-4">
      <div className="label">{label}</div>
      <div className="font-display text-2xl text-white">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-400">{hint}</div>}
    </div>
  );
}

export function Banner({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "danger" | "amber" | "success";
  title: string;
  children?: ReactNode;
}) {
  const tones = {
    info: "border-info/30 bg-info/8",
    danger: "border-danger/40 bg-danger/10",
    amber: "border-amber/35 bg-amber/8",
    success: "border-success/30 bg-success/8",
  } as const;
  return (
    <div className={`rounded-2xl border px-4 py-3 ${tones[tone]}`}>
      <div className="font-semibold text-white text-sm">{title}</div>
      {children && <div className="mt-1 text-sm text-slate-300 leading-relaxed">{children}</div>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="card p-10 text-center">
      <div className="font-display text-lg text-white">{title}</div>
      {children && <div className="mt-2 text-sm text-slate-400">{children}</div>}
    </div>
  );
}
