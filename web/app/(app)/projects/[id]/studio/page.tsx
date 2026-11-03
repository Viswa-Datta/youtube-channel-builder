import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { signedUrl } from "@/lib/storage";
import { Studio, type StudioScene } from "@/components/studio";
import { Badge } from "@/components/ui/badge";

export const metadata = { title: "Review Studio" };

export default async function StudioPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { sb } = await requireUser();
  const [{ data: p }, { data: render }, { data: scenes }, { data: assets }, { data: claims }, { data: signoff }] = await Promise.all([
    sb.from("projects").select("*").eq("id", id).single(),
    sb.from("renders").select("*").eq("project_id", id).eq("status", "succeeded").order("version", { ascending: false }).limit(1).maybeSingle(),
    sb.from("scenes").select("*").eq("project_id", id).order("idx"),
    sb.from("assets").select("*").eq("project_id", id),
    sb.from("research_claims").select("id,claim,status,source_refs").eq("project_id", id),
    sb.from("blur_signoffs").select("render_id").eq("project_id", id),
  ]);
  if (!p || !render) return <p className="text-sm">No finished render yet. <Link className="underline" href={`/projects/${id}`}>Back to the project</Link>.</p>;
  const thumbs = (assets ?? []).filter((a) => a.kind === "thumbnail");
  const name = (p.selected_title ?? p.title).replace(/[^\w]+/g, "-").slice(0, 60);
  const studioScenes: StudioScene[] = (scenes ?? []).map((s) => ({ ...s, start_sec: Number(s.start_sec ?? 0), audio_duration_sec: Number(s.audio_duration_sec ?? 0),
    assets: (assets ?? []).filter((a) => a.scene_id === s.id) }));
  const vtt = await signedUrl(render.srt_path?.replace(/\.srt$/, ".vtt"));

  return (
    <div className="mx-auto max-w-7xl space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto font-serif text-xl">{p.selected_title ?? p.title}</h1>
        <Badge variant="secondary">v{render.version}</Badge>
        <Badge variant={render.has_audio ? "success" : "danger"}>{render.has_audio ? "Narration audio ✓" : "NO AUDIO"}</Badge>
        <Badge variant="outline">{Number(render.integrated_lufs).toFixed(1)} LUFS</Badge>
      </div>
      <Studio
        projectId={id}
        renderId={render.id}
        video={(await signedUrl(render.mp4_path, 6 * 3600))!}
        vtt={vtt ?? ""}
        duration={Number(render.duration_sec)}
        scenes={studioScenes}
        claims={claims ?? []}
        titles={(p.production_pack?.seo?.titles as string[]) ?? []}
        selectedTitle={p.selected_title}
        thumbnails={await Promise.all(thumbs.map(async (t) => ({ id: t.id, selected: t.selected, url: await signedUrl(t.storage_path) })))}
        downloads={[
          { label: "MP4", url: await signedUrl(render.mp4_path, 3600, `${name}.mp4`) },
          { label: "Captions .srt", url: await signedUrl(render.srt_path, 3600, `${name}.srt`) },
          { label: "Script .txt", url: await signedUrl(render.script_path, 3600, `${name}-script.txt`) },
          { label: "Upload pack .txt", url: await signedUrl(render.pack_path, 3600, `${name}-youtube-pack.txt`) },
          { label: "Thumbnail", url: await signedUrl(render.thumb_path, 3600, `${name}-thumbnail.jpg`) },
        ]}
        voice={{ provider: p.voice_provider, id: p.voice_id, speed: Number(p.voice_speed) }}
        signoff={{ blur: (signoff ?? []).some((s) => s.render_id === render.id), factcheck: !!p.fact_check_signed_at, approved: !!p.approved_at }}
      />
    </div>
  );
}
