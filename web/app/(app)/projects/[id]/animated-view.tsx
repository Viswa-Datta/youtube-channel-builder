import { signedUrl } from "@/lib/storage";
import { costContext } from "@/lib/context";
import { shortSlots, tagString } from "@/lib/schedule";
import { JobWatcher } from "@/components/job-watcher";
import { GenerateButton } from "@/components/generate-button";
import { ShortsPanel, type ShortView } from "@/components/shorts-panel";
import { CharacterEditor } from "@/components/character-editor";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { supabaseServer } from "@/lib/supabase/server";

type SB = Awaited<ReturnType<typeof supabaseServer>>;
type Row = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const DEFAULT_LOOK = "a friendly man in his early thirties with short wavy dark-brown hair, a neat short beard, warm light-brown skin and brown eyes, slim build, wearing a mustard-yellow zip hoodie over a white T-shirt, dark navy cargo trousers and white sneakers; expressive, curious face";

/** Project page of an Animated Shorts channel: progress, the Short, its upload kit and the main character. */
export async function AnimatedView({ sb, p, ch, jobId }: { sb: SB; p: Row; ch: Row; jobId: string | null }) {
  const id = p.id as string;
  const [{ data: rows }, { data: jobs }, { data: claims }, { data: others }] = await Promise.all([
    sb.from("shorts").select("*").eq("project_id", id).order("idx"),
    sb.from("jobs").select("id,status,last_error").eq("project_id", id).eq("kind", "animated_short").order("created_at", { ascending: false }).limit(1),
    sb.from("research_claims").select("claim,status").eq("project_id", id).neq("status", "unknown").limit(40),
    sb.from("shorts").select("planned_at,posted_at,project_id,projects!inner(channel_id)").eq("projects.channel_id", p.channel_id).neq("project_id", id),
  ]);
  const last = jobs?.[0];
  const active = jobId ?? (last && ["queued", "running"].includes(last.status) ? last.id : null);
  const { ctx, defaultTier } = await costContext(8, p.tier);
  const plan = { days: ch.publish_days ?? ["mon", "wed", "fri"], time: ch.publish_time ?? "13:00", tz: ch.publish_tz ?? "America/Chicago" };
  const taken = ((others ?? []) as Row[]).map((x) => x.planned_at ?? x.posted_at).filter(Boolean) as string[];
  const all = (rows ?? []) as Row[];
  const free = shortSlots(plan, new Date(), taken, all.filter((r) => !r.planned_at).length, true);
  let n = 0;
  const name = (p.selected_title ?? p.title).replace(/[^\w]+/g, "-").slice(0, 60);
  const shorts: ShortView[] = await Promise.all(all.map(async (r) => ({
    id: r.id, idx: r.idx, status: r.status, error: r.error, title: r.title, hook: r.hook,
    description: [r.description ?? "", ch.youtube_url ? `🔔 Subscribe: ${String(ch.youtube_url).replace(/\/+$/, "")}?sub_confirmation=1` : ""]
      .filter(Boolean).join("\n\n").replace(/</g, "(").replace(/>/g, ")").slice(0, 5000),
    tags: tagString(["shorts", ...(r.hashtags ?? []), ...(p.tags ?? [])]),
    mp4: await signedUrl(r.mp4_path, 3600, `${name}.mp4`), srt: await signedUrl(r.srt_path, 3600, `${name}.srt`),
    duration: r.duration_sec ? Number(r.duration_sec) : null,
    slot: r.planned_at ?? (r.status === "succeeded" ? free[n++]?.toISOString() ?? null : null), slotIsSaved: !!r.planned_at,
    youtubeUrl: r.youtube_url, aiCount: ((r.lines ?? []) as Row[]).length,
    cover: await signedUrl(r.cover_path, 3600), coverDownload: await signedUrl(r.cover_path, 3600, `${name}-cover.jpg`),
    kind: "animated",
  })));
  const char = (ch.character ?? {}) as { name?: string; look?: string; ref_path?: string };
  const charImg = char.ref_path ? await signedUrl(char.ref_path, 3600) : null;
  const done = all.some((r) => r.status === "succeeded");

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Animated Short · {ch.name ?? ch.niche}</p>
          <h1 className="font-serif text-2xl">{p.selected_title ?? p.title}</h1>
        </div>
        {!active && !done && all.every((r) => r.status !== "running") && (
          <GenerateButton label="Make this Short" action="animated_short" minutes={8} defaultTier={defaultTier} ctx={ctx}
            endpoint={`/api/projects/${id}/animated`} payload={{}} />
        )}
      </div>
      {active && <JobWatcher jobId={active} label="Making the Short (research, script, scenes, animation, voice, cut: about 5–10 minutes)" />}
      {!active && last?.status === "failed" && !done && (
        <p className="text-sm text-destructive">The last attempt failed: {String(last.last_error ?? "").split("\n")[0]}</p>
      )}
      {all.length > 0 && <ShortsPanel projectId={id} tz={plan.tz} shorts={shorts} longTitle="" longPosted={false} busy={!!active} animated />}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Main character: {char.name ?? "Leo"}</CardTitle>
            <CardDescription>The same original character appears in every Short of this channel.</CardDescription>
          </CardHeader>
          <CardContent className="flex gap-4">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {charImg ? <img src={charImg} alt={char.name ?? "Main character"} className="aspect-[9/16] w-28 rounded border object-cover" />
              : <div className="flex aspect-[9/16] w-28 items-center justify-center rounded border text-center text-xs text-muted-foreground">Drawn with the first Short</div>}
            <div className="min-w-0 space-y-2 text-sm">
              <p className="text-muted-foreground">{char.look ?? DEFAULT_LOOK}</p>
              <CharacterEditor channelId={ch.id} name={char.name ?? "Leo"} look={char.look ?? DEFAULT_LOOK} />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Facts used</CardTitle>
            <CardDescription>From the research sources; the script may only use these.</CardDescription>
          </CardHeader>
          <CardContent>
            {claims?.length ? (
              <ul className="max-h-64 space-y-1 overflow-auto text-sm">
                {claims.map((c, i) => <li key={i} className="flex gap-2"><Badge variant={c.status === "confirmed" ? "success" : "warning"}>{c.status}</Badge><span>{c.claim}</span></li>)}
              </ul>
            ) : <p className="text-sm text-muted-foreground">Researched when the Short is made.</p>}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
