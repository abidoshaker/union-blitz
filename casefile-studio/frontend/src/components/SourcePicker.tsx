import { api } from "../lib/api";
import { useEffect, useState } from "react";

export interface ProviderInfo {
  name: string;
  label: string;
  kind?: string;
  is_archival?: boolean;
  is_local?: boolean;
  available: boolean;
  unavailable_reason: string;
  cost_per_image?: number;
}

interface Catalogue {
  images: ProviderInfo[];
  video: ProviderInfo[];
}

let cached: Catalogue | null = null;

/**
 * Forget the cached list.
 *
 * Adding an API key changes which providers are available, and without this
 * the picker keeps showing "needs a key" against a provider you have just
 * configured until the page is reloaded.
 */
export function forgetProviders(): void {
  cached = null;
}

/** One fetch per session; the list does not change while the app is running. */
export function useProviders(): Catalogue | null {
  const [catalogue, setCatalogue] = useState<Catalogue | null>(cached);
  useEffect(() => {
    if (cached) return;
    api
      .get<Catalogue>("/api/settings")
      .then((s) => {
        cached = { images: s.images ?? [], video: s.video ?? [] };
        setCatalogue(cached);
      })
      .catch(() => undefined);
  }, []);
  return catalogue;
}

/**
 * Choose any combination of libraries, rather than one and only one.
 *
 * A true-crime scene is better served by asking four places than by betting
 * the whole video on Pexels. Selecting several means each scene walks the list
 * until something comes back — and the starting point rotates per scene, so a
 * hundred consecutive frames do not all come from whichever one is first.
 */
export function SourcePicker({
  label,
  hint,
  providers,
  selected,
  onChange,
  showAi = false,
  emptyNote,
}: {
  label: string;
  hint?: string;
  providers: ProviderInfo[];
  selected: string[];
  onChange: (next: string[]) => void;
  showAi?: boolean;
  /** What an empty selection means here. It is not the same everywhere. */
  emptyNote?: string;
}) {
  const usable = providers.filter((p) => showAi || p.kind !== "ai");
  const toggle = (name: string) =>
    onChange(
      selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name],
    );

  const allNames = usable.filter((p) => p.available).map((p) => p.name);
  const everything = allNames.length > 0 && allNames.every((n) => selected.includes(n));

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="label !mb-0">{label}</span>
        {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
        <button
          className="ml-auto text-[11px] text-slate-400 underline-offset-2 hover:text-slate-200 hover:underline"
          onClick={() => onChange(everything ? [] : allNames)}
        >
          {everything ? "clear all" : "use everything available"}
        </button>
      </div>

      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {usable.map((p) => {
          const on = selected.includes(p.name);
          return (
            <button
              key={p.name}
              onClick={() => toggle(p.name)}
              disabled={!p.available && !on}
              title={p.available ? (p.is_archival ? "Archival — may hold the actual event" : "Stock — mood, not record") : p.unavailable_reason}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-colors ${
                on
                  ? "border-amber/60 bg-amber/15 text-amber"
                  : p.available
                    ? "border-white/10 bg-white/5 text-slate-300 hover:bg-white/10"
                    : "border-white/5 bg-transparent text-slate-600"
              }`}
            >
              <span className={on ? "text-amber" : "text-slate-600"}>{on ? "✓" : "+"}</span>
              {p.is_archival && <span className="text-[9px] text-success">●</span>}
              {p.label}
              {!p.available && <span className="text-[10px] text-slate-600">· needs a key</span>}
            </button>
          );
        })}
      </div>

      {selected.length === 0 && (
        <p className="mt-1 text-[11px] text-amber">
          {emptyNote ??
            "Nothing selected — sourcing falls back to this project's original single source."}
        </p>
      )}
      {selected.length > 1 && (
        <p className="mt-1 text-[11px] text-slate-500">
          Tried in order until one has something, starting at a different one each scene.
          <span className="ml-1 text-success">●</span> marks an archive.
        </p>
      )}
    </div>
  );
}
