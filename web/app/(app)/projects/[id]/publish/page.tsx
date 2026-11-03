import { requireUser } from "@/lib/supabase/server";
import { signedUrl } from "@/lib/storage";
import { plansConfig } from "@/lib/pricing";
import { PublishPanel } from "@/components/publish-panel";

export const metadata = { title: "Publish" };

export default async function PublishPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { sb } = await requireUser();
  const { data: p } = await sb.from("projects").select("*").eq("id", id).single();
  const { data: render } = await sb.from("renders").select("*").eq("project_id", id).eq("status", "succeeded").order("version", { ascending: false }).limit(1).maybeSingle();
  if (!p || !render) return <p className="text-sm">Render the video first.</p>;
  const [{ data: checklist }, { data: conn }, { data: pub }, { count }] = await Promise.all([
    sb.rpc("publish_checklist", { p_project: id, p_render: render.id }),
    sb.from("youtube_connection_status").select("*").maybeSingle(),
    sb.from("publishes").select("*").eq("render_id", render.id).maybeSingle(),
    sb.from("publishes").select("id", { count: "exact", head: true }).not("youtube_video_id", "is", null).gte("created_at", new Date(new Date().setHours(0, 0, 0, 0)).toISOString()),
  ]);
  const name = (p.selected_title ?? p.title).replace(/[^\w]+/g, "-").slice(0, 60);
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <h1 className="font-serif text-2xl">Publish: {p.selected_title ?? p.title}</h1>
      <PublishPanel
        projectId={id} renderId={render.id} checklist={checklist ?? { pass: false, items: [] }}
        connected={conn ? { title: conn.channel_title, audited: conn.audited_project } : null}
        existing={pub ? { status: pub.status, youtube_url: pub.youtube_url, privacy: pub.privacy, error: pub.error } : null}
        mp4={await signedUrl(render.mp4_path, 3600, `${name}.mp4`)} pack={await signedUrl(render.pack_path, 3600, `${name}-youtube-pack.txt`)}
        quota={{ usedToday: count ?? 0, dailyCap: plansConfig.youtube.daily_upload_cap_per_user }}
      />
    </div>
  );
}
