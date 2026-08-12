import { useEffect, useRef, useState } from "react";
import { api, type Job } from "./api";

/** Live job state, fed by the progress WebSocket with a polling safety net. */
export function useJobs(projectId?: number) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [connected, setConnected] = useState(false);
  const socket = useRef<WebSocket | null>(null);

  const refresh = async () => {
    const query = projectId ? `?project_id=${projectId}&limit=25` : "?limit=25";
    try {
      setJobs(await api.get<Job[]>(`/api/jobs${query}`));
    } catch {
      /* backend restarting */
    }
  };

  useEffect(() => {
    refresh();
    // Polling is the fallback, not the mechanism: a render emits hundreds of
    // progress events and we do not want a request per event.
    const timer = setInterval(refresh, 5000);

    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws/jobs`;
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(url);
      socket.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => setConnected(false);
      ws.onerror = () => setConnected(false);
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.kind === "job.progress") {
          setJobs((prev) =>
            prev.map((j) =>
              j.id === msg.job_id
                ? { ...j, progress: msg.progress, message: msg.message, eta_sec: msg.eta_sec }
                : j,
            ),
          );
        } else if (msg.kind?.startsWith("job.")) {
          refresh();
        }
      };
    } catch {
      setConnected(false);
    }

    return () => {
      clearInterval(timer);
      ws?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const active = jobs.filter((j) => ["queued", "running", "paused"].includes(j.status));
  return { jobs, active, connected, refresh };
}
