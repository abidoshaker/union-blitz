import {
  closestCenter,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { restrictToVerticalAxis } from "@dnd-kit/modifiers";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
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
const ROW = 168;

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
  const [dragging, setDragging] = useState<Scene | null>(null);
  const [note, setNote] = useState("");
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
    estimateSize: () => ROW,
    overscan: 8,
  });

  const sensors = useSensors(
    // A few pixels of travel before a drag starts, so clicking the checkbox or
    // the textarea inside a card still behaves like a click.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const ids = useMemo(() => scenes.map((s) => s.id), [scenes]);

  /**
   * Translate a drop inside the visible page into a position in the whole
   * project. The client only ever holds one page of a 240-scene project, so
   * the server is told which scenes moved and where they land, never a
   * renumbering of the page.
   *
   * The target is derived from whichever scene ends up *in front of* the
   * dragged one, not from the scene the cursor is over. By the time a drag
   * ends, dnd-kit has already shifted the surrounding cards to open a gap, so
   * the card under the cursor is no longer the one that was there when the
   * drag started — reading the position off it lands a row or two out.
   */
  const commitMove = async (moverIds: number[], from: number, to: number) => {
    const movers = scenes.filter((s) => moverIds.includes(s.id));
    const rearranged = arrayMove(scenes, from, to);

    let predecessor: Scene | null = null;
    for (let i = to - 1; i >= 0; i--) {
      if (!moverIds.includes(rearranged[i].id)) {
        predecessor = rearranged[i];
        break;
      }
    }

    // The server removes the movers first, so the target is expressed against
    // the list with them already taken out.
    const toIndex = predecessor
      ? predecessor.order_index -
        movers.filter((m) => m.order_index < predecessor!.order_index).length +
        1
      : 0;

    await api.post(`/api/projects/${projectId}/scenes/move`, {
      scene_ids: moverIds,
      to_index: Math.max(0, toIndex),
    });
    await load(0, true);
    onSceneChanged();
  };

  const onDragStart = (event: DragStartEvent) => {
    setNote("");
    setDragging(scenes.find((s) => s.id === event.active.id) ?? null);
  };

  const onDragEnd = async (event: DragEndEvent) => {
    const active = dragging;
    setDragging(null);
    const { over } = event;
    if (!active || !over || over.id === active.id) return;

    const from = scenes.findIndex((s) => s.id === active.id);
    const to = scenes.findIndex((s) => s.id === over.id);
    if (from < 0 || to < 0) return;

    // A drag on a selected card moves the whole selection.
    const moverIds =
      selected.has(active.id) && selected.size > 1
        ? scenes.filter((s) => selected.has(s.id)).map((s) => s.id)
        : [active.id];

    setScenes((prev) => arrayMove(prev, from, to));   // optimistic
    try {
      await commitMove(moverIds, from, to);
      setNote(
        moverIds.length > 1
          ? `Moved ${moverIds.length} scenes. Timings update on the next render.`
          : "Scene moved. Timings update on the next render.",
      );
    } catch (err) {
      setNote(`Could not move that scene: ${(err as Error).message}`);
      await load(0, true);
    }
  };

  const moveTo = async (scene: Scene, oneBased: number) => {
    const target = Math.max(0, Math.min(total - 1, oneBased - 1));
    if (target === scene.order_index) return;
    try {
      await api.post(`/api/projects/${projectId}/scenes/move`, {
        scene_ids: [scene.id],
        to_index: target,
      });
      await load(0, true);
      onSceneChanged();
      setNote(`Scene moved to position ${target + 1}.`);
    } catch (err) {
      setNote(`Could not move that scene: ${(err as Error).message}`);
    }
  };

  const nudge = async (scene: Scene, delta: number) => {
    await moveTo(scene, scene.order_index + 1 + delta);
  };

  const filtered = filter !== "" || chapterId != null;

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

      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>
          Drag the <span className="font-mono text-slate-300">⠿</span> handle to reorder, or use the
          arrows. For a long jump, type a position in the box on the card.
        </span>
        {filtered && (
          <span className="text-amber">
            · Reordering inside a filtered view moves scenes relative to what you can see.
          </span>
        )}
        {note && <span className="ml-auto text-info">{note}</span>}
      </div>

      {scenes.length === 0 ? (
        <Empty title={loading ? "Loading scenes…" : "No scenes match that filter"}>
          {!loading && "Split a script into scenes to see them here."}
        </Empty>
      ) : (
        <>
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            modifiers={[restrictToVerticalAxis]}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
            onDragCancel={() => setDragging(null)}
            // Rows mount and unmount as the list auto-scrolls under a drag, so
            // droppable rects have to be re-measured continuously.
            measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
          >
            <SortableContext items={ids} strategy={verticalListSortingStrategy}>
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
                        <SortableScene
                          scene={scene}
                          position={scene.order_index + 1}
                          total={total}
                          checked={selected.has(scene.id)}
                          onToggle={() => {
                            const next = new Set(selected);
                            next.has(scene.id) ? next.delete(scene.id) : next.add(scene.id);
                            onSelect(next);
                          }}
                          onChanged={() => {
                            load(0, true);
                            onSceneChanged();
                          }}
                          onMoveTo={(n) => moveTo(scene, n)}
                          onNudge={(d) => nudge(scene, d)}
                        />
                      </div>
                    );
                  })}
                </div>
              </div>
            </SortableContext>

            <DragOverlay>
              {dragging && (
                <div className="card border-magenta/60 p-3 opacity-95 shadow-glow">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-slate-500">
                      #{dragging.order_index + 1}
                    </span>
                    <span className="line-clamp-1 text-sm text-slate-200">{dragging.text}</span>
                  </div>
                  {selected.has(dragging.id) && selected.size > 1 && (
                    <div className="mt-1 text-xs text-magenta">
                      moving {selected.size} selected scenes
                    </div>
                  )}
                </div>
              )}
            </DragOverlay>
          </DndContext>

          {scenes.length < total && (
            <button
              className="btn-ghost w-full"
              onClick={() => load(offset + PAGE, false)}
              disabled={loading}
            >
              {loading ? "Loading…" : `Load ${Math.min(PAGE, total - scenes.length)} more`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function SortableScene(props: {
  scene: Scene;
  position: number;
  total: number;
  checked: boolean;
  onToggle: () => void;
  onChanged: () => void;
  onMoveTo: (position: number) => void;
  onNudge: (delta: number) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: props.scene.id,
  });

  return (
    <div
      ref={setNodeRef}
      style={{
        // The virtualiser owns the outer element's position, so dnd-kit's
        // transform is applied here, on the inner card.
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.35 : 1,
      }}
      className="h-full"
    >
      <SceneCard {...props} handleProps={{ ...attributes, ...listeners }} />
    </div>
  );
}

function SceneCard({
  scene,
  position,
  total,
  checked,
  onToggle,
  onChanged,
  onMoveTo,
  onNudge,
  handleProps,
}: {
  scene: Scene;
  position: number;
  total: number;
  checked: boolean;
  onToggle: () => void;
  onChanged: () => void;
  onMoveTo: (position: number) => void;
  onNudge: (delta: number) => void;
  handleProps: Record<string, unknown>;
}) {
  const [text, setText] = useState(scene.text);
  const [dirty, setDirty] = useState(false);
  const [target, setTarget] = useState(String(position));

  useEffect(() => {
    setText(scene.text);
    setDirty(false);
  }, [scene.id, scene.text]);

  useEffect(() => {
    setTarget(String(position));
  }, [position]);

  const save = async () => {
    await api.patch(`/api/scenes/${scene.id}`, { text });
    setDirty(false);
    onChanged();
  };

  return (
    <div
      className={`card h-full p-3 flex gap-2 transition-colors ${
        checked ? "border-magenta/60 bg-magenta/5" : ""
      } ${scene.depicts_real_person ? "tape" : ""}`}
    >
      <div className="flex flex-col items-center gap-1 pt-0.5">
        <button
          {...handleProps}
          aria-label={`Reorder scene ${position}`}
          title="Drag to reorder · or focus and use the arrow keys"
          className="cursor-grab rounded-lg px-1.5 py-1 text-slate-500 hover:bg-white/10 hover:text-slate-200 active:cursor-grabbing"
        >
          ⠿
        </button>
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="h-4 w-4 accent-[#FF2D75]"
        />
        <div className="mt-0.5 flex flex-col">
          <button
            className="px-1 text-xs text-slate-500 hover:text-amber disabled:opacity-25"
            disabled={position <= 1}
            onClick={() => onNudge(-1)}
            title="Move up one"
          >
            ▲
          </button>
          <button
            className="px-1 text-xs text-slate-500 hover:text-amber disabled:opacity-25"
            disabled={position >= total}
            onClick={() => onNudge(1)}
            title="Move down one"
          >
            ▼
          </button>
        </div>
      </div>

      <div className="w-40 shrink-0">
        {scene.image_url ? (
          <img
            src={scene.image_url}
            alt=""
            className="h-[120px] w-full rounded-xl border border-white/10 object-cover"
            loading="lazy"
            draggable={false}
          />
        ) : (
          <div className="grid h-[120px] w-full place-items-center rounded-xl border border-dashed border-white/15 text-xs text-slate-500">
            no image
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          <input
            className="w-14 rounded-md border border-white/10 bg-base/70 px-1.5 py-0.5 text-center font-mono text-xs text-slate-300 outline-none focus:border-amber/60"
            value={target}
            title={`Position ${position} of ${total} — type a number and press Enter`}
            onChange={(e) => setTarget(e.target.value.replace(/\D/g, ""))}
            onKeyDown={(e) => {
              if (e.key === "Enter" && target) onMoveTo(Number(target));
            }}
            onBlur={() => setTarget(String(position))}
          />
          {scene.duration > 0 && <Pill>{formatDuration(scene.duration)}</Pill>}
          <Pill tone={scene.media_kind === "video" ? "info" : "slate"}>
            {scene.media_kind === "video" ? "video" : "photo"}
          </Pill>
          <Pill tone={scene.has_audio ? "success" : "slate"}>
            {scene.has_audio ? "audio" : "no audio"}
          </Pill>
          {scene.blur_faces && <Pill tone="magenta">faces blurred</Pill>}
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
          {scene.media_kind === "video" ? (
            <select
              className="rounded-md border border-white/10 bg-base/70 px-1.5 py-0.5 text-xs text-slate-300 outline-none focus:border-amber/60"
              value={scene.audio_mode}
              title="What happens to the clip's own sound"
              onChange={async (e) => {
                await api.patch(`/api/scenes/${scene.id}`, { audio_mode: e.target.value });
                onChanged();
              }}
            >
              <option value="narration">clip muted · voiceover only</option>
              <option value="soundbite">clip speaks · voiceover pauses</option>
              <option value="ambient">clip under voiceover · ducked</option>
            </select>
          ) : null}
          <span className="truncate text-xs text-slate-500" title={scene.image_prompt}>
            {scene.image_prompt}
          </span>
          {dirty && (
            <button className="btn-amber ml-auto !px-3 !py-1 text-xs" onClick={save}>
              Save · re-records audio
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
