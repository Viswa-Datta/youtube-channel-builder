"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { LENGTHS } from "@/components/cost-modal";

/** Video length menu on the project page. Disabled only while the video is being rendered. */
export function LengthSelect({ projectId, minutes, disabled, rendered }: { projectId: string; minutes: number; disabled?: boolean; rendered?: boolean }) {
  const router = useRouter();
  const [value, setValue] = useState(minutes);
  const [busy, setBusy] = useState(false);
  return (
    <select aria-label="Video length" value={value} disabled={disabled || busy}
      title={disabled ? "Cancel the video being made to change its length" : "Video length"}
      className="rounded-md border bg-background px-2 py-1 text-sm disabled:opacity-60"
      onChange={async (e) => {
        const m = Number(e.target.value);
        if (rendered && !window.confirm(`Change to ${m} minutes? The script is rewritten and the voice re-recorded at the next render. Research and footage are kept.`)) return;
        setBusy(true);
        const r = await fetch(`/api/projects/${projectId}/length`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_minutes: m }) });
        setBusy(false);
        if (r.ok) setValue(m);
        else window.alert((await r.json().catch(() => ({}))).error ?? "Could not change the length.");
        router.refresh();
      }}>
      {LENGTHS.map((m) => <option key={m} value={m}>{m} min</option>)}
    </select>
  );
}
