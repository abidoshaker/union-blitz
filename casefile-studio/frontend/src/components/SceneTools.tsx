import { useEffect, useRef, useState } from "react";
import { api, formatDuration, type Scene } from "../lib/api";
import { Banner, Modal, Pill, Spinner } from "./ui";

/**
 * Watch one scene exactly as it will appear in the finished video - Ken Burns,
 * burned captions, overlays, face blur, its own audio - without rendering the
 * other two hundred.
 */
export function ScenePreview({ scene, onClose }: { scene: Scene; onClose: () => void }) {
  const [state, setState] = useState<"building" | "ready" | "error">("building");
  const [message, setMessage] = useState("");
  const [src, setSrc] = useState("");

  const build = async (force: boolean) => {
    setState("building");
    setMessage("");
    try {
      const res = await api.post<{ url: string; duration: number }>(
        `/api/scenes/${scene.id}/preview${force ? "?force=true" : ""}`,
      );
      // Cache-bust, or the browser replays the previous take.
      setSrc(`${res.url}?t=${Date.now()}`);
      setState("ready");
    } catch (err) {
      setMessage((err as Error).message);
      setState("error");
    }
  };

  useEffect(() => {
    build(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene.id]);

  return (
    <Modal title={`Scene ${scene.order_index + 1} preview`} onClose={onClose} wide>
      {state === "building" && <Spinner label="Rendering this scene…" />}

      {state === "error" && (
        <Banner tone="danger" title="Could not build the preview">
          {message}
        </Banner>
      )}

      {state === "ready" && (
        <div className="space-y-3">
          <video
            src={src}
            controls
            autoPlay
            className="w-full rounded-xl border border-white/10 bg-black"
          />
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <Pill tone={scene.media_kind === "video" ? "info" : "slate"}>
              {scene.media_kind === "video" ? "video" : "photo"}
            </Pill>
            {scene.duration > 0 && <Pill>{formatDuration(scene.duration)}</Pill>}
            {scene.blur_faces && <Pill tone="magenta">faces blurred</Pill>}
            <span className="ml-auto">{scene.attribution || scene.license}</span>
            <button className="btn-ghost !px-3 !py-1" onClick={() => build(true)}>
              Rebuild
            </button>
          </div>
          <p className="text-xs text-slate-500">
            This is the finished scene, not an approximation. The only thing missing is the
            crossfade into the next scene, which is added during the full render.
          </p>
        </div>
      )}
    </Modal>
  );
}

interface Candidate {
  url: string;
  thumb: string;
  license: string;
  attribution: string;
  provider: string;
  width: number;
  height: number;
  title: string;
}

// Archives first: these are where an actual photograph of the event will be,
// if a free one exists at all. Stock libraries below them are mood, not record.
const SOURCES = [
  { key: "nara", label: "US National Archives", archival: true },
  { key: "loc", label: "Library of Congress", archival: true },
  { key: "wikimedia", label: "Wikimedia", archival: true },
  { key: "internet_archive_image", label: "Internet Archive", archival: true },
  { key: "pexels", label: "Pexels", archival: false },
  { key: "pixabay", label: "Pixabay", archival: false },
];

const AI_SOURCES = [
  { key: "openai_image", label: "OpenAI" },
  { key: "fal_flux", label: "Flux (fal.ai)" },
  { key: "placeholder", label: "Placeholder (free, offline)" },
];

type Mode = "search" | "generate" | "url";

/** Pick a different picture for one scene, or upload your own. */
export function ImagePicker({
  scene,
  onClose,
  onApplied,
}: {
  scene: Scene;
  onClose: () => void;
  onApplied: () => void;
}) {
  const [mode, setMode] = useState<Mode>("search");
  const [provider, setProvider] = useState(scene.depicts_real_person ? "wikimedia" : "pexels");
  const [aiProvider, setAiProvider] = useState("placeholder");
  const [prompt, setPrompt] = useState(scene.image_prompt || "");
  const [url, setUrl] = useState("");
  const [term, setTerm] = useState("");
  const [usedQuery, setUsedQuery] = useState("");
  const [results, setResults] = useState<Candidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const search = async () => {
    setBusy(true);
    setNote("");
    setResults([]);
    try {
      const res = await api.post<{ query: string; results: Candidate[] }>(
        `/api/scenes/${scene.id}/search-images`,
        { provider, query: term || undefined, count: 16 },
      );
      setResults(res.results);
      setUsedQuery(res.query);
      if (res.results.length === 0) {
        setNote(`Nothing came back for "${res.query}". Try different words or another source.`);
      }
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    // A message about a missing search key means nothing on the AI tab.
    setNote("");
    if (mode === "search") search();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, mode]);

  const generate = async () => {
    setBusy(true);
    setNote("");
    try {
      await api.post(`/api/scenes/${scene.id}/generate-image`, {
        provider: aiProvider,
        prompt: prompt || undefined,
      });
      onApplied();
      onClose();
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const fromUrl = async () => {
    setBusy(true);
    setNote("");
    try {
      await api.post(`/api/scenes/${scene.id}/image-from-url`, { url });
      onApplied();
      onClose();
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const choose = async (candidate: Candidate) => {
    setBusy(true);
    setNote("");
    try {
      await api.post(`/api/scenes/${scene.id}/choose-image`, {
        url: candidate.url,
        provider: candidate.provider,
        license: candidate.license,
        attribution: candidate.attribution,
        title: candidate.title,
        width: candidate.width,
        height: candidate.height,
      });
      onApplied();
      onClose();
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File) => {
    setBusy(true);
    setNote("");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`/api/scenes/${scene.id}/upload-image`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
      onApplied();
      onClose();
    } catch (err) {
      setNote((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`Picture for scene ${scene.order_index + 1}`} onClose={onClose} wide>
      <div className="space-y-3">
        <p className="text-sm text-slate-400">{scene.text}</p>

        {scene.depicts_real_person && (
          <Banner tone="danger" title="Flagged as a real person">
            AI generation is blocked for this scene. Use archival or licensed photography.
          </Banner>
        )}

        <div className="flex gap-1 rounded-xl bg-white/5 p-1">
          {(["search", "generate", "url"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded-lg px-4 py-1.5 text-sm font-semibold transition-colors ${
                mode === m ? "bg-accent text-white" : "text-slate-300 hover:bg-white/5"
              }`}
            >
              {m === "search" ? "Search libraries" : m === "generate" ? "Make with AI" : "From a link"}
            </button>
          ))}
          <button
            className="btn-ghost ml-auto"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
          >
            Upload mine
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
        </div>

        {mode === "search" && (
          <>
            <div className="flex flex-wrap gap-2">
              {SOURCES.map((s) => (
                <button
                  key={s.key}
                  className={s.key === provider ? "btn-amber" : "btn-ghost"}
                  title={s.archival ? "Archival — may hold the actual event" : "Stock — mood, not record"}
                  onClick={() => setProvider(s.key)}
                >
                  {s.archival && <span className="mr-1 text-[10px] text-success">●</span>}
                  {s.label}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                className="input"
                placeholder={usedQuery ? `Searched: ${usedQuery}` : "Search words…"}
                value={term}
                onChange={(e) => setTerm(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && search()}
              />
              <button className="btn-primary shrink-0" onClick={search} disabled={busy}>
                Search
              </button>
            </div>
          </>
        )}

        {mode === "generate" && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              {AI_SOURCES.map((s) => (
                <button
                  key={s.key}
                  className={s.key === aiProvider ? "btn-amber" : "btn-ghost"}
                  onClick={() => setAiProvider(s.key)}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <textarea
              className="input h-24 resize-none text-sm"
              placeholder="Describe the picture you want…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <button
              className="btn-primary"
              onClick={generate}
              disabled={busy || scene.depicts_real_person}
            >
              {scene.depicts_real_person ? "Blocked on real-person scenes" : "Generate"}
            </button>
            <p className="text-xs text-slate-500">
              A dramatisation, not a record. Turn on the disclaimer overlay for scenes that use one.
            </p>
          </div>
        )}

        {mode === "url" && (
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                className="input font-mono text-xs"
                placeholder="https://…/photograph.jpg"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && url && fromUrl()}
              />
              <button className="btn-primary shrink-0" onClick={fromUrl} disabled={busy || !url}>
                Use it
              </button>
            </div>
            <p className="text-xs text-slate-500">
              For images you have already licensed or downloaded. The app records it as supplied by
              you — checking you have the right to publish it is on you.
            </p>
          </div>
        )}

        {note && <div className="text-sm text-amber">{note}</div>}
        {busy && <Spinner label="Working…" />}

        {results.length > 0 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {results.map((c) => (
              <button
                key={c.url}
                onClick={() => choose(c)}
                disabled={busy}
                className="group overflow-hidden rounded-xl border border-white/10 text-left transition-all hover:border-magenta/60 hover:shadow-glow"
                title={`${c.title}\n${c.license}`}
              >
                <img
                  src={c.thumb || c.url}
                  alt=""
                  loading="lazy"
                  className="h-28 w-full bg-base object-cover"
                />
                <div className="p-2">
                  <div className="truncate text-[11px] text-slate-300">{c.title || c.provider}</div>
                  <div className="truncate text-[10px] text-slate-500">{c.license}</div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}
