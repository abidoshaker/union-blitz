import { useEffect } from "react";
import { createPortal } from "react-dom";
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


export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    // Stop the page scrolling behind the dialog.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  // Rendered through a portal, not in place. The storyboard puts a CSS
  // transform on every row (the virtualiser positions them, dnd-kit animates
  // them), and a transformed ancestor becomes the containing block for
  // position:fixed - so an inline dialog would be trapped inside its card
  // instead of covering the page.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={`card max-h-[90vh] w-full overflow-auto p-5 ${wide ? "max-w-5xl" : "max-w-2xl"}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-3">
          <h3 className="text-lg">{title}</h3>
          <button className="btn-ghost ml-auto !px-3 !py-1" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 py-8 text-sm text-slate-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/20 border-t-magenta" />
      {label}
    </div>
  );
}

export function Confirm({
  title,
  body,
  confirmLabel,
  danger = false,
  onConfirm,
  onCancel,
  busy = false,
}: {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="text-sm leading-relaxed text-slate-300">{body}</div>
      <div className="mt-5 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          className={danger ? "btn-danger" : "btn-primary"}
          onClick={onConfirm}
          disabled={busy}
        >
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
