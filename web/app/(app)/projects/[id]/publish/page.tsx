import { requireUser } from "@/lib/supabase/server";
import { signedUrl } from "@/lib/storage";
import { UploadKit } from "@/components/upload-kit";
import { finalDescription, nextFreeSlot, tagString } from "@/lib/schedule";

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
    </div>
  );
}
