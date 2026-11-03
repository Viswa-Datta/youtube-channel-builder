"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { X, ChevronRight, ChevronLeft } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CostModal, type CostContext } from "@/components/cost-modal";
import type { Tier } from "@/lib/pricing-core";
import { usd } from "@/lib/utils";

type Idea = {
  id: string; title: string; hook: string; angle: string; pillar: string | null; demand_score: number; competition_score: number;
  feasibility_score: number; est_cost_usd: number; rank_score: number; status: string; rationale: string | null;
  case_info?: { case_name?: string; location?: string; year?: number; agency?: string; case_status?: string;
    public_footage?: string[]; reference?: { title: string; url: string } | null; youtube_checked?: boolean; youtube_error?: string } | null;
  footage_evidence?: { kind: string; source: string; title: string; url: string; official?: boolean; channel?: string | null; media_type?: string }[] | null;
  case_verified?: boolean;
};

/** Real case details + what the live footage search actually found. */
function CaseDetails({ i }: { i: Idea }) {
  const c = i.case_info;
  if (!c?.case_name) return null;
  const ev = i.footage_evidence ?? [];
  const official = ev.filter((e) => e.official).length;
  const files = ev.filter((e) => e.kind === "downloadable").length;
  return (
    <div className="mt-2 space-y-1 rounded border bg-muted/40 p-2 text-xs">
      <div className="font-medium">{c.case_name}</div>
      <div className="text-muted-foreground">{[c.location, c.year, c.agency, c.case_status].filter(Boolean).join(" · ")}</div>
      <div className="flex flex-wrap gap-1">
        {i.case_verified
          ? <a href={c.reference?.url} target="_blank" rel="noreferrer"><Badge variant="success">Case confirmed ↗</Badge></a>
          : <Badge variant="danger">Not confirmed</Badge>}
        {official > 0 && <Badge variant="success">{official} official video{official > 1 ? "s" : ""}</Badge>}
        {files > 0 && <Badge variant="secondary">{files} free file{files > 1 ? "s" : ""}</Badge>}
        {c.youtube_error ? <Badge variant="danger" title={c.youtube_error}>YouTube search failed</Badge>
          : c.youtube_checked === false && <Badge variant="outline">YouTube not searched</Badge>}
      </div>
      {ev.length > 0 && (
        <details>
          <summary className="cursor-pointer text-muted-foreground">Footage found ({ev.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {ev.slice(0, 8).map((e) => (
              <li key={e.url} className="truncate">
                <a className="underline" href={e.url} target="_blank" rel="noreferrer">{e.title || e.url}</a>
                <span className="text-muted-foreground"> · {e.source}{e.channel ? ` · ${e.channel}` : ""}{e.official ? " · official" : ""}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
const COLS = [
  ["suggested", "Suggested"], ["shortlisted", "Shortlisted"], ["in_production", "In Production"], ["ready", "Ready"], ["published", "Published"],
] as const;

/** Kanban: Suggested -> Shortlisted -> In Production -> Ready -> Published. Drag on desktop, arrow buttons on phones. */
export function IdeaBoard({ ideas, ctx, defaultTier, minutes }: { ideas: Idea[]; ctx: CostContext; defaultTier: Tier; minutes: number }) {
  const router = useRouter();
  const [items, setItems] = useState(ideas);
  // When the page refreshes with new ideas (e.g. after the Ideas Agent finishes), show them.
  useEffect(() => setItems(ideas), [ideas]);
  const [pick, setPick] = useState<Idea | null>(null);
  const [drag, setDrag] = useState<string | null>(null);
  const [col, setCol] = useState<string>("suggested"); // phone: one column at a time

  const move = async (id: string, status: string) => {
    setItems((xs) => xs.map((x) => (x.id === id ? { ...x, status } : x)));
    await fetch(`/api/ideas/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) });
  };
  const manual = (s: string) => s === "suggested" || s === "shortlisted";

  return (
    <>
      <div className="mb-3 flex gap-1 overflow-x-auto md:hidden">
        {COLS.map(([k, label]) => (
          <button key={k} onClick={() => setCol(k)} className={`shrink-0 rounded-full border px-3 py-1 text-xs ${col === k ? "border-accent bg-accent/10" : ""}`}>
            {label} ({items.filter((i) => i.status === k).length})
          </button>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-5">
        {COLS.map(([k, label], ci) => (
          <div key={k} className={`min-w-0 rounded-lg bg-muted/60 p-2 ${col === k ? "" : "hidden md:block"}`}
            onDragOver={(e) => manual(k) && e.preventDefault()} onDrop={() => drag && manual(k) && move(drag, k)}>
            <div className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
            <div className="space-y-2">
              {items.filter((i) => i.status === k).sort((a, b) => b.rank_score - a.rank_score).map((i) => (
                <div key={i.id} draggable={manual(i.status)} onDragStart={() => setDrag(i.id)} className="rounded-md border bg-card p-2.5 text-sm shadow-sm">
                  <div className="flex items-start justify-between gap-1">
                    <span className="font-medium leading-snug">{i.title}</span>
                    {manual(i.status) && (
                      <button onClick={() => move(i.id, "dismissed")} aria-label="Dismiss" className="shrink-0 text-muted-foreground hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{i.hook}</p>
                  <CaseDetails i={i} />
                  <div className="mt-2 flex flex-wrap gap-1">
                    <Badge variant="secondary">Rank {Number(i.rank_score).toFixed(1)}</Badge>
                    <Badge variant="outline">Demand {i.demand_score}</Badge>
                    <Badge variant="outline">Competition {i.competition_score}</Badge>
                    <Badge variant={i.feasibility_score >= 7 ? "success" : i.feasibility_score >= 5 ? "warning" : "danger"}>Footage {i.feasibility_score}</Badge>
                    <Badge variant="outline">≈{usd(i.est_cost_usd)}</Badge>
                  </div>
                  {manual(i.status) && (
                    <div className="mt-2 flex items-center justify-between gap-1">
                      <div className="flex gap-1">
                        {ci > 0 && <Button size="sm" variant="ghost" onClick={() => move(i.id, COLS[ci - 1][0])} aria-label="Move left"><ChevronLeft className="h-3.5 w-3.5" /></Button>}
                        {k === "suggested" && <Button size="sm" variant="ghost" onClick={() => move(i.id, "shortlisted")}>Queue <ChevronRight className="h-3.5 w-3.5" /></Button>}
                      </div>
                      <Button size="sm" onClick={() => setPick(i)}>Pick</Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {pick && (
        <CostModal open={!!pick} onOpenChange={(o) => !o && setPick(null)} action="production_pack" minutes={minutes} title={`Production pack: ${pick.title}`}
          defaultTier={defaultTier} ctx={ctx}
          onConfirm={async (tier) => {
            const r = await fetch("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idea_id: pick.id, tier }) });
            const j = await r.json();
            if (!r.ok) throw new Error(j.error);
            router.push(`/projects/${j.id}?job=${j.job.id}`);
          }} />
      )}
    </>
  );
}
