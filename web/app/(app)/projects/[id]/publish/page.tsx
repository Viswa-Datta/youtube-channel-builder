import { requireUser } from "@/lib/supabase/server";
import { signedUrl } from "@/lib/storage";
import { UploadKit } from "@/components/upload-kit";
import { finalDescription, nextFreeSlot, shortDescription, shortSlots, tagString } from "@/lib/schedule";
import { ShortsPanel, type ShortView } from "@/components/shorts-panel";
import { GenerateButton } from "@/components/generate-button";
import { JobWatcher } from "@/components/job-watcher";
import { costContext } from "@/lib/context";

export const metadata = { title: "Publish" };

export default async function PublishPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { sb } = await requireUser();
  const { data: p } = await sb.from("projects").select("*").eq("id", id).single();
  const { data: render } = await sb.from("renders").select("*").eq("project_id", id).eq("status", "succeeded").order("version", { ascending: false }).limit(1).maybeSingle();
  if (!p || !render) return <p className="text-sm">Render the video first.</p>;
  const [{ data: checklist }, { data: ch }, { data: pub }, { data: kit }] = await Promise.all([
    sb.rpc("publish_checklist", { p_project: id, p_render: render.id }),
    sb.from("channels").select("*").eq("id", p.channel_id).single(),
    sb.from("publishes").select("*").eq("render_id", render.id).maybeSingle(),
    sb.from("channel_kits").select("selected").eq("channel_id", p.channel_id).order("version", { ascending: false }).limit(1).maybeSingle(),
  ]);
  // Other videos of this channel: their slots are taken, and posted ones become "watch next" links.
  const { data: siblings } = await sb.from("publishes")
    .select("render_id,planned_at,posted_at,youtube_url,project_id,projects!inner(channel_id,selected_title,title)")
    .eq("projects.channel_id", p.channel_id).neq("project_id", id);
  type Sib = { planned_at: string | null; posted_at: string | null; youtube_url: string | null; projects: { selected_title: string | null; title: string } | { selected_title: string | null; title: string }[] };
  const sibs = (siblings ?? []) as unknown as Sib[];
  const plan = { days: ch?.publish_days ?? ["tue", "sat"], time: ch?.publish_time ?? "13:00", tz: ch?.publish_tz ?? "America/Chicago" };
  const taken = sibs.map((s) => s.planned_at ?? s.posted_at).filter(Boolean) as string[];
  const slot = pub?.planned_at ?? nextFreeSlot(plan, taken).toISOString();
  const watchNext = sibs.filter((s) => s.youtube_url)
    .sort((a, b) => String(b.posted_at ?? b.planned_at).localeCompare(String(a.posted_at ?? a.planned_at)))
    .slice(0, 2)
    .map((s) => { const pr = Array.isArray(s.projects) ? s.projects[0] : s.projects; return { title: pr.selected_title ?? pr.title, url: s.youtube_url! }; });
  const name = (p.selected_title ?? p.title).replace(/[^\w]+/g, "-").slice(0, 60);
  // Shorts: made from this documentary, planned on the days between long videos, after it comes out.
  const [{ data: shortRows }, { data: shortJobs }, { data: otherShorts }] = await Promise.all([
    sb.from("shorts").select("*").eq("project_id", id).order("idx"),
    sb.from("jobs").select("id,status").eq("project_id", id).eq("kind", "shorts").in("status", ["queued", "running"]).order("created_at", { ascending: false }).limit(1),
    sb.from("shorts").select("planned_at,posted_at,project_id,projects!inner(channel_id)").eq("projects.channel_id", p.channel_id).neq("project_id", id),
  ]);
  const shortJob = shortJobs?.[0]?.id ?? null;
  const rows = (shortRows ?? []) as Record<string, any>[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  const takenShort = [...((otherShorts ?? []) as { planned_at: string | null; posted_at: string | null }[]).map((x) => x.planned_at ?? x.posted_at),
    ...rows.map((r) => r.planned_at)].filter(Boolean) as string[];
  const freeShortSlots = shortSlots(plan, pub?.posted_at ?? slot, takenShort, rows.filter((r) => !r.planned_at).length);
  let nFree = 0;
  const longUrl = pub?.youtube_url ?? null;
  const shorts: ShortView[] = await Promise.all(rows.map(async (r) => {
    const fname = `${name}-short-${r.idx + 1}`;
    const s = r.planned_at ?? (r.status === "succeeded" ? freeShortSlots[nFree++]?.toISOString() ?? null : null);
    return {
      id: r.id, idx: r.idx, status: r.status, error: r.error, title: r.title, hook: r.hook,
      description: shortDescription(r.description ?? "", longUrl, ch?.youtube_url ?? null),
      tags: tagString(["shorts", ...(r.hashtags ?? []), ...(p.tags ?? [])]),
      mp4: r.mp4_path ? await signedUrl(r.mp4_path, 3600, `${fname}.mp4`) : null,
      srt: r.srt_path ? await signedUrl(r.srt_path, 3600, `${fname}.srt`) : null,
      duration: r.duration_sec ? Number(r.duration_sec) : null, slot: s, slotIsSaved: !!r.planned_at,
      youtubeUrl: r.youtube_url, aiCount: ((r.lines ?? []) as { ai?: boolean }[]).filter((x) => x.ai).length,
      cover: r.cover_path ? await signedUrl(r.cover_path, 3600) : null,
      coverDownload: r.cover_path ? await signedUrl(r.cover_path, 3600, `${fname}-cover.jpg`) : null,
    };
  }));
  const { ctx, defaultTier } = await costContext(2, p.tier);
  const items = ((checklist?.items ?? []) as { key: string; label: string; pass: boolean; detail: string }[]);
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <h1 className="font-serif text-2xl">Publish: {p.selected_title ?? p.title}</h1>
      <UploadKit
        projectId={id} renderId={render.id} channelId={p.channel_id}
        channelName={(kit?.selected as { name?: string } | null)?.name ?? ch?.name ?? ""}
        plan={{ ...plan, youtube_url: ch?.youtube_url ?? null }}
        slot={slot} slotIsSaved={!!pub?.planned_at} youtubeUrl={pub?.youtube_url ?? null}
        title={p.selected_title ?? p.title} description={finalDescription(p.description ?? "", watchNext, ch?.youtube_url ?? null)}
        tags={tagString(p.tags)} watchNextCount={watchNext.length}
        mp4={await signedUrl(render.mp4_path, 3600, `${name}.mp4`)}
        thumb={render.thumb_path ? await signedUrl(render.thumb_path, 3600, `${name}-thumbnail.jpg`) : null}
        srt={render.srt_path ? await signedUrl(render.srt_path, 3600, `${name}.srt`) : null}
        checklist={items}
      />
      <section className="space-y-3 pt-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="font-serif text-xl">Shorts</h2>
            <p className="text-sm text-muted-foreground">Vertical 45–55 s Shorts with their own script and hook, made from this documentary&apos;s research.</p>
          </div>
          {!shortJob && (
            <GenerateButton label={rows.length ? "Make 2 more Shorts" : "Make 2 Shorts"} action="shorts" minutes={2}
              defaultTier={defaultTier} ctx={ctx} endpoint={`/api/projects/${id}/shorts`} payload={{ n: 2 }} variant={rows.length ? "outline" : "default"} />
          )}
        </div>
        {shortJob && <JobWatcher jobId={shortJob} label="Making Shorts" />}
        {rows.length > 0 && <ShortsPanel projectId={id} tz={plan.tz} shorts={shorts} longTitle={p.selected_title ?? p.title}
          longPosted={!!longUrl} busy={!!shortJob} />}
      </section>
    </div>
  );
}
