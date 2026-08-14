export type Json = Record<string, any>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.message ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: Json) =>
    request<T>(p, { method: "POST", body: JSON.stringify(body ?? {}) }),
  patch: <T>(p: string, body: Json) =>
    request<T>(p, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T = void>(p: string) => request<T>(p, { method: "DELETE" }),
};

export interface Project {
  id: number;
  title: string;
  status: string;
  settings: Json;
  scene_count: number;
  scenes_ready: number;
  scenes_with_audio: number;
  scenes_with_image: number;
  flagged_real_person: number;
  word_count: number;
  estimated_runtime_sec: number;
  narration_sec: number;
  latest_render: string;
  render_count: number;
}

export interface Scene {
  id: number;
  order_index: number;
  chapter_id: number | null;
  text: string;
  image_prompt: string;
  visual_source: string;
  media_kind: string;
  media_in: number;
  audio_mode: string;
  blur_faces: boolean;
  match_level: string;
  source_query: string;
  kenburns: string;
  status: string;
  duration: number;
  start_time: number;
  depicts_real_person: boolean;
  ai_disclaimer: boolean;
  has_audio: boolean;
  has_image: boolean;
  image_url: string;
  audio_url: string;
  license: string;
  attribution: string;
}

export interface Job {
  id: number;
  type: string;
  status: string;
  progress: number;
  message: string;
  eta_sec: number | null;
  error: string;
  result: Json;
  project_id: number | null;
}

export interface Chapter {
  id: number;
  title: string;
  order_index: number;
  start_time: number;
  end_time: number;
  scene_count: number;
}

export interface Preflight {
  ok: boolean;
  problems: string[];
  estimated_disk_bytes: number;
  free_disk_bytes: number;
  estimated_render_sec_low: number;
  estimated_render_sec_high: number;
  workers: number;
  scene_count: number;
  estimated_video_sec: number;
  narration_cost_usd: number;
  hw_encoders: string[];
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return "0:00";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export function formatRange(low: number, high: number): string {
  const unit = (v: number) => (v >= 3600 ? `${(v / 3600).toFixed(1)} h` : `${Math.round(v / 60)} min`);
  return `${unit(low)} – ${unit(high)}`;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
}
