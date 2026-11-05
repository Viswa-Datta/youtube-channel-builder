"use client";
import { useEffect, useState } from "react";
import { Moon, Sun, Monitor } from "lucide-react";

type Mode = "light" | "dark" | "system";

/** Runs before first paint (inlined in <head>) so the page never flashes the wrong theme. */
export const THEME_SCRIPT = `(function(){try{var m=localStorage.getItem('theme')||'system';var d=m==='dark'||(m==='system'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

function apply(mode: Mode) {
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [mode, setMode] = useState<Mode>("system");
  useEffect(() => {
    let m: Mode = "system";
    try { m = (localStorage.getItem("theme") as Mode) || "system"; } catch {}
    setMode(m);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => { let cur: Mode = "system"; try { cur = (localStorage.getItem("theme") as Mode) || "system"; } catch {} if (cur === "system") apply("system"); };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  const choose = (m: Mode) => {
    setMode(m);
    try { localStorage.setItem("theme", m); } catch {}
    apply(m);
  };
  const opts: [Mode, typeof Sun, string][] = [["light", Sun, "Light"], ["dark", Moon, "Dark"], ["system", Monitor, "Auto"]];
  return (
    <div className={`inline-flex rounded-md border p-0.5 ${compact ? "" : "text-xs"}`} role="radiogroup" aria-label="Theme">
      {opts.map(([m, Icon, label]) => (
        <button key={m} role="radio" aria-checked={mode === m} title={label} onClick={() => choose(m)}
          className={`flex items-center gap-1 rounded px-2 py-1 ${mode === m ? "bg-accent/20 text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
          <Icon className="h-3.5 w-3.5" />{!compact && <span>{label}</span>}
        </button>
      ))}
    </div>
  );
}
