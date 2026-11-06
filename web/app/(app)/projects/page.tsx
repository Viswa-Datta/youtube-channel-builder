import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { Badge } from "@/components/ui/badge";
import { CancelRenderButton } from "@/components/cancel-render-button";

const ACTIVE = ["queued", "running", "waiting_user", "paused_cap"];

export const metadata = { title: "Projects" };

export default async function Projects() {
  const { sb } = await requireUser();
  const { data } = await sb.from("projects").select("id,title,status,tier,target_minutes,created_at,renders(id,status,version,progress)").order("created_at", { ascending: false });
  return (
    <div className="mx-auto max-w-5xl space-y-3">
      <h1 className="font-serif text-2xl">Projects</h1>
      {(data ?? []).map((p) => {
        const latest = ((p.renders as { id: string; status: string; version: number; progress: number }[] | null) ?? [])
          .filter((r) => ACTIVE.includes(r.status)).sort((a, b) => a.version - b.version)[0];
        const active = !!latest;
        return (
          <div key={p.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-card p-3 hover:bg-muted">
            <Link href={`/projects/${p.id}`} className="min-w-0 flex-1 font-medium">{p.title}</Link>
            <span className="flex flex-wrap items-center gap-1">
              <Badge variant="outline">{p.target_minutes} min</Badge><Badge variant="outline">{p.tier}</Badge>
              <Badge variant="secondary">{active ? `making video · ${Math.round(Number(latest.progress))}%` : p.status}</Badge>
              {active && <CancelRenderButton renderId={latest.id} />}
            </span>
          </div>
        );
      })}
      {!data?.length && <p className="text-sm text-muted-foreground">Pick an idea on an Idea Board to start a project.</p>}
    </div>
  );
}
