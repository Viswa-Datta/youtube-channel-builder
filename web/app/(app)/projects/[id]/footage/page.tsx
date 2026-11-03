import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { signedUrl } from "@/lib/storage";
import { FootageDesk, type DeskAsset, type DeskRequest, type DeskScene } from "@/components/footage-desk";

export const metadata = { title: "Footage Desk" };

export default async function FootagePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { sb } = await requireUser();
  const [{ data: p }, { data: render }, { data: scenes }, { data: reqs }, { data: assets }] = await Promise.all([
    sb.from("projects").select("id,title,selected_title").eq("id", id).single(),
    sb.from("renders").select("id,status,stage").eq("project_id", id).order("version", { ascending: false }).limit(1).maybeSingle(),
    sb.from("scenes").select("id,idx,title").eq("project_id", id).order("idx"),
    sb.from("help_requests").select("*").eq("project_id", id).order("created_at"),
    sb.from("assets").select("*").eq("project_id", id).in("kind", ["clip", "image"]).in("tier", ["real", "news_document"]).order("created_at"),
  ]);
  if (!p) return <p className="text-sm">Project not found.</p>;
  const desk: DeskAsset[] = await Promise.all((assets ?? []).map(async (a) => ({
    id: a.id, scene_id: a.scene_id, kind: a.kind, tier: a.tier, title: a.title, source_name: a.source_name, source_url: a.source_url,
    agency: a.agency, release_date: a.release_date, license: a.license, rights_notes: a.rights_notes, rights_checked: a.rights_checked,
    selected: a.selected, in_sec: a.in_sec === null ? null : Number(a.in_sec), out_sec: a.out_sec === null ? null : Number(a.out_sec),
    preview: a.storage_path && a.kind === "image" ? await signedUrl(a.storage_path, 3600) : ((a.meta?.thumb_url as string) ?? null),
    dismissed: !!a.meta?.dismissed, uploaded: !!a.meta?.uploaded_by_user,
  })));
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <h1 className="font-serif text-2xl">Footage Desk</h1>
        <p className="text-sm text-muted-foreground">{p.selected_title ?? p.title} · <Link className="underline" href={`/projects/${id}`}>Back to the project</Link></p>
      </div>
      <FootageDesk projectId={id} renderId={render?.id ?? null} waiting={render?.status === "waiting_user"}
        scenes={(scenes ?? []) as DeskScene[]}
        requests={(reqs ?? []).map((r) => ({ ...r, start_sec: r.start_sec === null ? null : Number(r.start_sec), end_sec: r.end_sec === null ? null : Number(r.end_sec) })) as DeskRequest[]}
        assets={desk} />
    </div>
  );
}
