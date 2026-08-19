import { useState } from "react";
import { Dashboard } from "./pages/Dashboard";
import { SettingsPanel } from "./pages/SettingsPanel";
import { Workspace } from "./pages/Workspace";

type View = { name: "dashboard" } | { name: "project"; id: number } | { name: "settings" };

export default function App() {
  const [view, setView] = useState<View>({ name: "dashboard" });

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-white/10 bg-base/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-6 py-3">
          <button
            onClick={() => setView({ name: "dashboard" })}
            className="flex items-center gap-2.5"
          >
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-accent font-display text-lg font-bold text-white shadow-glow">
              C
            </span>
            <span className="font-display text-lg tracking-tight text-white">CaseFile Studio</span>
          </button>
          <nav className="ml-auto flex gap-1">
            <button
              className={view.name === "dashboard" ? "btn-amber" : "btn-ghost"}
              onClick={() => setView({ name: "dashboard" })}
            >
              Projects
            </button>
            <button
              className={view.name === "settings" ? "btn-amber" : "btn-ghost"}
              onClick={() => setView({ name: "settings" })}
            >
              Settings
            </button>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-6 py-6">
        {view.name === "dashboard" && (
          <Dashboard onOpen={(id) => setView({ name: "project", id })} />
        )}
        {view.name === "project" && (
          <Workspace projectId={view.id} onBack={() => setView({ name: "dashboard" })} />
        )}
        {view.name === "settings" && <SettingsPanel />}
      </main>
    </div>
  );
}
