import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { plansConfig } from "@/lib/pricing";
import { formatSlot } from "@/lib/schedule";

export const metadata = { title: "Publish" };

export default async function PublishIndex() {
  const { sb } = await requireUser();
  const [{ data: ready }, { data: pubs }, { data: quota }] = await Promise.all([
    sb.from("projects").select("id,title,selected_title,status").in("status", ["review", "approved", "published"]).order("updated_at", { ascending: false }),
    sb.from("publishes").select("id,status,privacy,youtube_url,created_at,project_id").order("created_at", { ascending: false }).limit(20),
    sb.from("youtube_quota").select("day,units").order("day", { ascending: false }).limit(14),
  ]);
  const { data: planned } = await sb.from("publishes")
    .select("id,planned_at,youtube_url,project_id,projects!inner(selected_title,title,channels!inner(publish_tz))")
    .not("planned_at", "is", null).gte("planned_at", new Date(Date.now() - 7 * 86_400_000).toISOString())
    .order("planned_at", { ascending: true });
  type Row = { id: string; planned_at: string; youtube_url: string | null; project_id: string;
    projects: { selected_title: string | null; title: string; channels: { publish_tz: string } } };
  const upcoming = (planned ?? []) as unknown as Row[];
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <h1 className="font-serif text-2xl">Publish</h1>
      {upcoming.length > 0 && (
        <Card><CardHeader><CardTitle className="text-base">Release schedule</CardTitle></CardHeader>
          <CardContent className="space-y-1.5 text-sm">{upcoming.map((u) => (
            <Link key={u.id} href={`/projects/${u.project_id}/publish`} className="flex flex-wrap items-center justify-between gap-2 rounded-md p-1.5 hover:bg-muted">
              <span className="min-w-0"><b>{formatSlot(u.planned_at, u.projects.channels.publish_tz)}</b> · {u.projects.selected_title ?? u.projects.title}</span>
              <Badge variant={u.youtube_url ? "secondary" : "outline"}>{u.youtube_url ? "scheduled on YouTube" : "upload kit ready"}</Badge>
            </Link>
          ))}</CardContent></Card>
      )}
      <div className="space-y-2">
        {(ready ?? []).map((p) => (
          <Link key={p.id} href={`/projects/${p.id}/publish`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-card p-3 hover:bg-muted">
            <span className="min-w-0">{p.selected_title ?? p.title}</span><Badge variant="secondary">{p.status}</Badge>
          </Link>
        ))}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card><CardHeader><CardTitle className="text-base">Uploads</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm">{(pubs ?? []).map((u) => (
            <div key={u.id} className="flex flex-wrap gap-2"><Badge variant="outline">{u.status}</Badge><Badge variant="outline">{u.privacy}</Badge>
              {u.youtube_url ? <a className="underline" href={u.youtube_url}>{u.youtube_url}</a> : <span className="text-muted-foreground">{new Date(u.created_at).toLocaleString()}</span>}</div>
          ))}</CardContent></Card>
        <Card><CardHeader><CardTitle className="text-base">YouTube API quota (units/day)</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p className="text-xs text-muted-foreground">The project default is {plansConfig.youtube.quota_units_per_day_project.toLocaleString()} units/day; an upload costs about {plansConfig.youtube.quota_cost["videos.insert"]} units (verify current costs in Google Cloud). Reads are cached.</p>
            {(quota ?? []).map((q) => <div key={q.day} className="flex justify-between"><span>{q.day}</span><span className="tabular-nums">{q.units}</span></div>)}
          </CardContent></Card>
      </div>
    </div>
  );
}
