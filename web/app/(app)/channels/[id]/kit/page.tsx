import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { costContext } from "@/lib/context";
import { KitEditor } from "@/components/kit-editor";
import { JobWatcher } from "@/components/job-watcher";
import { GenerateButton } from "@/components/generate-button";
import { Button } from "@/components/ui/button";

export const metadata = { title: "Channel kit" };

export default async function KitPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ job?: string }> }) {
  const { id } = await params;
  const { job } = await searchParams;
  const { sb } = await requireUser();
  const { data: ch } = await sb.from("channels").select("*").eq("id", id).single();
  const { data: kit } = await sb.from("channel_kits").select("*").eq("channel_id", id).order("version", { ascending: false }).limit(1).maybeSingle();
  const { ctx, defaultTier } = await costContext();
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="font-serif text-2xl">Channel kit</h1>
          <p className="text-sm text-muted-foreground">{ch?.niche} · version {kit?.version ?? "–"}</p>
        </div>
        <div className="flex gap-2">
          <GenerateButton label={kit ? "Regenerate whole kit" : "Generate kit"} variant="outline" action="channel_kit" minutes={ch?.target_minutes ?? 12}
            defaultTier={defaultTier} ctx={ctx} endpoint="/api/jobs" payload={{ kind: "channel_kit", channel_id: id }}
            redirectTo={`/channels/${id}/kit?job={job}`} />
          <Button asChild><Link href={`/channels/${id}/ideas`}>Idea board →</Link></Button>
        </div>
      </div>
      {job && <JobWatcher jobId={job} label="Channel Agent" />}
      {kit ? (
        <KitEditor channelId={id} kitId={kit.id} kit={kit.data} selected={kit.selected} ctx={ctx} defaultTier={defaultTier} minutes={ch?.target_minutes ?? 12} />
      ) : (
        <p className="text-sm text-muted-foreground">No kit yet.</p>
      )}
      <div className="rounded-md border bg-card p-4 text-sm">
        <h2 className="font-medium">Create the channel on YouTube</h2>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-muted-foreground">
          <li>Go to youtube.com → your avatar → Create a channel (the YouTube API cannot do this step).</li>
          <li>Paste the name, handle, description and About text from this kit; upload artwork made from the prompts.</li>
          <li>Come back to Settings → Connect YouTube to link it with upload-only and read-only permissions.</li>
        </ol>
      </div>
    </div>
  );
}
