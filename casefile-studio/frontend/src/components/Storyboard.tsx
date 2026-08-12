import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";
import { api, formatDuration, type Chapter, type Scene } from "../lib/api";
import { Empty, Pill } from "./ui";

const FILTERS = [
  { key: "", label: "All" },
  { key: "needs_image", label: "Needs image" },
  { key: "needs_audio", label: "Needs audio" },
  { key: "real_person", label: "Real person" },
  { key: "ready", label: "Ready" },
];

const PAGE = 60;

/**
 * A 240-scene storyboard is virtualised and filtered, never a flat grid.
 * Only the visible rows are in the DOM, and only one page is in memory.
 */
export function Storyboard({
  projectId,
  chapters,
  selected,
  onSelect,
  onSceneChanged,
}: {
  projectId: number;
  chapters: Chapter[];
  selected: Set<number>;
  onSelect: (ids: Set<number>) => void;
  onSceneChanged: () => void;
}) {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState("");
  const [chapterId, setChapterId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const parentRef = useRef<HTMLDivElement>(null);

  const load = async (nextOffset = 0, replace = true) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ offset: String(nextOffset), limit: String(PAGE) });
      if (filter) params.set("filter", filter);
      if (chapterId != null) params.set("chapter_id", String(chapterId));
      const page = await api.get<{ total: number; items: Scene[] }>(
        `/api/projects/${projectId}/scenes?${params}`,
      );
      setTotal(page.total);
      setScenes((prev) => (replace ? page.items : [...prev, ...page.items]));
      setOffset(nextOffset);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(0, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, filter, chapterId]);

  const rowVirtualizer = useVirtualizer({
    count: scenes.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 168,
    overscan: 6,
  });

  const toggle = (id: number) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    onSelect(next);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={f.key === filter ? "btn-amber" : "btn-ghost"}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
        <select
          className="input max-w-[220px]"
          value={chapterId ?? ""}
          onChange={(e) => setChapterId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All chapters</option>
          {chapters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title} · {c.scene_count} scenes
            </option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-2 text-sm text-slate-400">
          <span className="font-mono">
            {scenes.length}/{total}
          </span>
          <button className="btn-ghost" onClick={() => onSelect(new Set(scenes.map((s) => s.id)))}>
            Select page
          </button>
          <button className="btn-ghost" onClick={() => onSelect(new Set())}>
            Clear
          </button>
        </div>
      </div>

      {scenes.length === 0 ? (
        <Empty title={loading ? "Loading scenes…" : "No scenes match that filter"}>
          {!loading && "Split a script into scenes to see them here."}
        </Empty>
      ) : (
        <>
          <div ref={parentRef} className="h-[62vh] overflow-auto rounded-2xl">
            <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
              {rowVirtualizer.getVirtualItems().map((row) => {
                const scene = scenes[row.index];
                return (
                  <div
                    key={scene.id}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      height: row.size,
                      transform: `translateY(${row.start}px)`,
                    }}
                    className="pb-3 pr-1"
                  >
                    <SceneCard
                      scene={scene}
                      checked={selected.has(scene.id)}
                      onToggle={() => toggle(scene.id)}
                      onChanged={() => {
                        load(0, true);
                        onSceneChanged();
                      }}
                    />
                  </div>
                );
              })}
            </div>
          </div>
          {scenes.length < total && (
            <button className="btn-ghost w-full" onClick={() => load(offset + PAGE, false)} disabled={loading}>
              {loading ? "Loading…" : `Load ${Math.min(PAGE, total - scenes.length)} more`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function SceneCard({
  scene,
  checked,
  onToggle,
  onChanged,
}: {
  scene: Scene;
  checked: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const [text, setText] = useState(scene.text);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setText(scene.text);
    setDirty(false);
  }, [scene.id, scene.text]);

  const save = async () => {
    await api.patch(`/api/scenes/${scene.id}`, { text });
    setDirty(false);
    onChanged();
  };

  return (
    <div
      className={`card h-full p-3 flex gap-3 transition-colors ${
        checked ? "border-magenta/60 bg-magenta/5" : ""
      } ${scene.depicts_real_person ? "tape" : ""}`}
    >
      <input type="checkbox" checked={checked} onChange={onToggle} className="mt-1 h-4 w-4 accent-[#FF2D75]" />

      <div className="w-40 shrink-0">
        {scene.image_url ? (
          <img
            src={scene.image_url}
            alt=""
            className="h-[120px] w-full rounded-xl object-cover border border-white/10"
            loading="lazy"
          />
        ) : (
          <div className="grid h-[120px] w-full place-items-center rounded-xl border border-dashed border-white/15 text-xs text-slate-500">
            no image
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-xs text-slate-500">#{scene.order_index + 1}</span>
          {scene.duration > 0 && <Pill>{formatDuration(scene.duration)}</Pill>}
          <Pill tone={scene.has_audio ? "success" : "slate"}>{scene.has_audio ? "audio" : "no audio"}</Pill>
          {scene.depicts_real_person && <Pill tone="danger">real person · archival only</Pill>}
          {scene.ai_disclaimer && <Pill tone="amber">disclaimer</Pill>}
        </div>

        <textarea
          className="input h-[62px] resize-none text-sm leading-snug"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setDirty(true);
          }}
        />

        <div className="mt-1.5 flex items-center gap-2">
          <span className="truncate text-xs text-slate-500" title={scene.image_prompt}>
            {scene.image_prompt}
          </span>
          {dirty && (
            <button className="btn-amber ml-auto !py-1 !px-3 text-xs" onClick={save}>
              Save · re-records audio
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
