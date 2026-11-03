"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";

type Job = {
  id: string; kind: string; stage: string | null; status: string; progress: number; progress_note: string | null;
  attempts: number; max_attempts: number; last_error: string | null; missing_keys: string | null;
  render: { status: string; stage: string; progress: number; error: string | null } | null;
};

/** Polls a background job. The browser never holds the work open: closing the tab does not stop it. */
export function JobWatcher({ jobId, label }: { jobId: string; label: string }) {
  const [job, setJob] = useState<Job | null>(null);
  const router = useRouter();
  const seen = useRef<string>("");
  useEffect(() => {
    let alive = true;
    let t: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const r = await fetch(`/api/jobs/${jobId}`);
      if (r.ok && alive) {
        const j: Job = await r.json();
        setJob(j);
        // Refresh the page once each time the outcome changes (finished, or paused for the user at the Footage Desk).
        const state = j.render?.status === "waiting_user" ? "waiting" : j.status === "succeeded" && (!j.render || j.render.status === "succeeded") ? "done" : j.status === "failed" ? "failed" : "";
        if (state && seen.current !== state) {
          seen.current = state;
          router.refresh();
        }
      }
      if (alive) t = setTimeout(tick, 3000);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [jobId, router]);
  if (!job) return null;
  const pct = job.render ? Number(job.render.progress) : Number(job.progress);
  return (
    <div className="rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{label}</span>
        <Badge variant={job.status === "failed" ? "danger" : job.status === "paused_cap" ? "warning" : job.status === "succeeded" ? "success" : "secondary"}>
          {job.render ? `${job.render.stage ?? job.stage} · ${job.render.status}` : job.status}
        </Badge>
      </div>
      <Progress value={pct} className="mt-2" />
      <div className="mt-1 text-xs text-muted-foreground">
        {job.progress_note ?? ""} {job.attempts > 1 ? `· attempt ${job.attempts}/${job.max_attempts} (resumes where it stopped)` : ""}
      </div>
      {job.missing_keys && (
        <p className="mt-2 text-xs text-destructive">Stopped: missing configuration {job.missing_keys}. Nothing was generated or charged. See Settings → Setup.</p>
      )}
      {job.status === "paused_cap" && <p className="mt-2 text-xs text-warning">Paused before exceeding your monthly cap. Raise it in Settings to continue.</p>}
      {job.status === "failed" && !job.missing_keys && <p className="mt-2 text-xs text-destructive">{job.render?.error ?? job.last_error?.split("\n")[0]}</p>}
    </div>
  );
}
