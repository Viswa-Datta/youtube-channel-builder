import { requireUser } from "@/lib/supabase/server";
import { costContext } from "@/lib/context";
import { IdeaBoard } from "@/components/idea-board";
import { JobWatcher } from "@/components/job-watcher";
import { GenerateButton } from "@/components/generate-button";

export const metadata = { title: "Idea board" };

export default async function IdeasPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ job?: string }> }) {
  const { id } = await params;
  const { job } = await searchParams;
  const { sb } = await requireUser();
  const { data: ch } = await sb.from("channels").select("*").eq("id", id).single();
  const { data: ideas } = await sb.from("ideas").select("*").eq("channel_id", id).neq("status", "dismissed");
  const { ctx, defaultTier } = await costContext();
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="font-serif text-2xl">Idea board</h1>
          <p className="text-sm text-muted-foreground">{ch?.name ?? ch?.niche}. You pick the topic; nothing is produced without your pick.</p>
        </div>
        <GenerateButton label={ideas?.length ? "Generate more ideas" : "Generate 25 ideas"} action="ideas" minutes={ch?.target_minutes ?? 12}
          defaultTier={defaultTier} ctx={ctx} endpoint="/api/jobs" payload={{ kind: "ideas", channel_id: id, count: 25 }}
          redirectTo={`/channels/${id}/ideas?job={job}`} />
      </div>
      {job && <JobWatcher jobId={job} label="Ideas Agent" />}
      <p className="text-xs text-muted-foreground">Rank = 0.40 × demand + 0.25 × (10 − competition) + 0.35 × footage feasibility. Cost is the estimated full-video cost at your default tier.</p>
      <IdeaBoard ideas={(ideas ?? []).map((i) => ({ ...i, rank_score: Number(i.rank_score), est_cost_usd: Number(i.est_cost_usd) }))}
        ctx={ctx} defaultTier={defaultTier} minutes={ch?.target_minutes ?? 12} />
    </div>
  );
}
