import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { usd } from "@/lib/utils";

export const metadata = { title: "Dashboard" };

export default async function Dashboard() {
  const { sb } = await requireUser();
  const [{ data: channels }, { data: projects }, { data: profile }, { data: ledger }, { data: yt }] = await Promise.all([
    sb.from("channels").select("id,name,niche,target_minutes,cadence").order("created_at"),
    sb.from("projects").select("id,title,status,tier,target_minutes,created_at,renders(id,version,status,stage,progress)").order("created_at", { ascending: false }).limit(8),
    sb.from("users").select("*").single(),
    sb.from("cost_ledger").select("cost_usd,created_at").gte("created_at", new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString()),
    sb.from("youtube_connection_status").select("*").maybeSingle(),
  ]);
  const spend = (ledger ?? []).reduce((a, r) => a + Number(r.cost_usd), 0);
  const cap = Number(profile?.monthly_cap_usd ?? 0);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl">Dashboard</h1>
          <p className="text-sm text-muted-foreground">Niche → channel kit → ideas → production pack → whole video → review → publish.</p>
        </div>
        <Button asChild><Link href="/channels/new">New channel</Link></Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card><CardHeader><CardDescription>Spend this month</CardDescription><CardTitle className="text-2xl">{usd(spend)}</CardTitle></CardHeader>
          <CardContent><Progress value={cap ? (spend / cap) * 100 : 0} /><p className="mt-1 text-xs text-muted-foreground">Hard cap {usd(cap)} · <Link className="underline" href="/settings">change</Link></p></CardContent></Card>
        <Card><CardHeader><CardDescription>YouTube</CardDescription><CardTitle className="text-base">{yt ? yt.channel_title : "Not connected"}</CardTitle></CardHeader>
          <CardContent className="text-xs text-muted-foreground">{yt ? (yt.audited_project ? "Uploads can be public/scheduled." : "Uploads are private until the Google API audit passes.") : <Link className="underline" href="/settings">Connect in Settings</Link>}</CardContent></Card>
        <Card><CardHeader><CardDescription>Plan</CardDescription><CardTitle className="text-base capitalize">{profile?.plan}</CardTitle></CardHeader>
          <CardContent className="text-xs text-muted-foreground"><Link className="underline" href="/costs">Subscription map</Link></CardContent></Card>
      </div>

      <section>
        <h2 className="mb-2 font-medium">Channels</h2>
        {!channels?.length ? (
          <Card><CardContent className="p-5 text-sm">No channels yet. <Link className="underline" href="/channels/new">Start with your niche</Link>.</CardContent></Card>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {channels.map((c) => (
              <Card key={c.id}>
                <CardHeader>
                  <CardTitle>{c.name ?? c.niche}</CardTitle>
                  <CardDescription>{c.niche} · {c.target_minutes} min · {c.cadence ?? "cadence not set"}</CardDescription>
                </CardHeader>
                <CardContent className="flex gap-2">
                  <Button size="sm" variant="outline" asChild><Link href={`/channels/${c.id}/kit`}>Channel kit</Link></Button>
                  <Button size="sm" variant="outline" asChild><Link href={`/channels/${c.id}/ideas`}>Idea board</Link></Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 font-medium">Recent projects</h2>
        <div className="space-y-2">
          {(projects ?? []).map((p) => {
            const r = (p.renders as { version: number; status: string; stage: string; progress: number }[] | null)?.sort((a, b) => b.version - a.version)[0];
            return (
              <Link key={p.id} href={`/projects/${p.id}`} className="block rounded-md border bg-card p-3 hover:bg-muted">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0 truncate font-medium">{p.title}</span>
                  <Badge variant={p.status === "failed" ? "danger" : p.status === "published" ? "success" : "secondary"}>{p.status}</Badge>
                </div>
                {r && r.status === "running" && <Progress className="mt-2" value={Number(r.progress)} />}
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}
