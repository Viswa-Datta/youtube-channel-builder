import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { costContext } from "@/lib/context";
import { JobWatcher } from "@/components/job-watcher";
import { GenerateButton } from "@/components/generate-button";
import { FoiaLog } from "@/components/foia-log";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata = { title: "Production pack" };

const claimVariant = { confirmed: "success", disputed: "warning", unknown: "danger" } as const;

export default async function ProjectPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ job?: string }> }) {
  const { id } = await params;
  const { job } = await searchParams;
  const { sb } = await requireUser();
  const [{ data: p }, { data: scenes }, { data: claims }, { data: sources }, { data: plan }, { data: redactions }, { data: foia }, { data: renders }, { data: jobs }] = await Promise.all([
    sb.from("projects").select("*").eq("id", id).single(),
    sb.from("scenes").select("*").eq("project_id", id).order("idx"),
    sb.from("research_claims").select("*").eq("project_id", id).order("scene_idx"),
    sb.from("research_sources").select("*").eq("project_id", id).order("ref"),
    sb.from("footage_plan").select("*").eq("project_id", id).order("scene_idx").order("priority"),
    sb.from("redaction_plan").select("*").eq("project_id", id).is("asset_id", null),
    sb.from("foia_requests").select("*").eq("project_id", id).order("created_at"),
    sb.from("renders").select("*").eq("project_id", id).order("version", { ascending: false }),
    sb.from("jobs").select("id,kind,status,render_id").eq("project_id", id).in("status", ["queued", "running", "paused_cap"]).order("created_at", { ascending: false }).limit(1),
  ]);
  if (!p) return <p>Not found</p>;
  const pack = p.production_pack as null | { seo: { titles: string[]; description: string; tags: string[]; thumbnail_concept: string; thumbnail_prompt: string }; research: { disclaimers: string[]; fact_check_notes: string[]; gaps: string[]; summary: string }; chapters: { t: string; title: string }[]; footage: { foia_suggestions: string[] } };
  const { ctx, defaultTier } = await costContext(p.target_minutes, p.tier);
  const activeJob = job ?? jobs?.[0]?.id;
  const latest = renders?.[0];

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-serif text-2xl">{p.title}</h1>
          <p className="text-sm text-muted-foreground">{p.target_minutes} min · {p.tier} · status {p.status}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {p.status !== "pack_pending" && (
            <GenerateButton label={latest ? "Re-render changed scenes" : "Generate whole video"} action="whole_video" minutes={p.target_minutes}
              defaultTier={p.tier} ctx={ctx} endpoint={`/api/projects/${id}/render`} payload={{}}
              redirectTo={`/projects/${id}?job={job}`} />
          )}
          {latest && <Button variant="outline" asChild><Link href={`/projects/${id}/footage`}>Footage Desk</Link></Button>}
          {latest?.status === "succeeded" && <Button variant="outline" asChild><Link href={`/projects/${id}/studio`}>Review Studio</Link></Button>}
        </div>
      </div>
      {latest?.status === "waiting_user" && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-accent bg-accent/10 p-3 text-sm">
          <span><b>Your video is paused at the Footage Desk.</b> The app found footage to review and has a few things only you can do (like saving an official clip from YouTube).</span>
          <Button asChild><Link href={`/projects/${id}/footage`}>Open the Footage Desk →</Link></Button>
        </div>
      )}
      {activeJob && <JobWatcher jobId={activeJob} label={p.status === "pack_pending" ? "Research → Script → Visual Director → SEO" : "Whole video render"} />}

      {!pack ? <p className="text-sm text-muted-foreground">The production pack appears here when the agents finish.</p> : (
        <>
          <Card>
            <CardHeader><CardTitle>Script, scene by scene</CardTitle><CardDescription>Draft (60–120 words per scene). The whole-video render expands each scene to full narration.</CardDescription></CardHeader>
            <CardContent className="space-y-3">
              {(scenes ?? []).map((s) => {
                const fp = (plan ?? []).filter((f) => f.scene_idx === s.idx);
                return (
                  <div key={s.id} className="rounded-md border p-3">
                    <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{s.idx + 1}. {s.title}</span>
                      {s.word_count > 0 && <Badge variant="secondary">{s.word_count} words narrated</Badge>}</div>
                    <p className="mt-1 text-sm">{s.draft}</p>
                    <div className="mt-2 space-y-1">
                      {fp.map((f) => (
                        <div key={f.id} className="flex flex-wrap items-center gap-2 text-xs">
                          <Badge variant={f.tier === "real" ? "default" : f.tier === "news_document" ? "secondary" : "outline"}>{f.tier === "stock_broll" ? "B-roll (stock)" : f.tier === "real" ? "Real footage" : "News / document"}</Badge>
                          <span className="text-muted-foreground">{f.description}</span>
                          {f.status === "gap" && <Badge variant="danger">GAP — search “{f.search_query}” · {f.source_hint}</Badge>}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>Research and fact-check</CardTitle><CardDescription>{pack.research.summary}</CardDescription></CardHeader>
              <CardContent className="space-y-2 text-sm">
                {(claims ?? []).map((c) => (
                  <div key={c.id} className="flex items-start gap-2">
                    <Badge variant={claimVariant[c.status as keyof typeof claimVariant]}>{c.status}</Badge>
                    <span>{c.claim} <span className="text-xs text-muted-foreground">{c.source_refs.join(", ")}</span>{c.note && <span className="block text-xs text-muted-foreground">{c.note}</span>}</span>
                  </div>
                ))}
                {pack.research.disclaimers.length > 0 && <p className="rounded bg-muted p-2 text-xs">Disclaimers: {pack.research.disclaimers.join(" ")}</p>}
                <div className="text-xs text-muted-foreground"><b>Fact-check notes:</b> {pack.research.fact_check_notes.join(" · ")}</div>
                <div className="text-xs text-muted-foreground"><b>Gaps:</b> {pack.research.gaps.join(" · ")}</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Sources (court record first)</CardTitle></CardHeader>
              <CardContent className="space-y-1.5 text-sm">
                {(sources ?? []).map((s) => (
                  <div key={s.id}><Badge variant="outline">{s.ref}</Badge> <Badge variant="secondary">{s.tier.replace("_", " ")}</Badge>{" "}
                    {s.url ? <a className="underline" href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}
                    <span className="text-xs text-muted-foreground"> — {s.publisher} {s.published_at ?? ""}</span></div>
                ))}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>Titles, description, tags</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-sm">
                <ul className="list-disc pl-5">{pack.seo.titles.map((t) => <li key={t}>{t} <span className="text-xs text-muted-foreground">({t.length})</span></li>)}</ul>
                <div className="text-xs"><b>Chapters:</b> {pack.chapters.map((c) => `${c.t} ${c.title}`).join(" · ")}</div>
                <div className="flex flex-wrap gap-1">{pack.seo.tags.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}</div>
                <p className="text-xs text-muted-foreground"><b>Thumbnail:</b> {pack.seo.thumbnail_concept}</p>
                <p className="text-xs text-muted-foreground"><b>Thumbnail prompt:</b> {pack.seo.thumbnail_prompt}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Blur / redaction plan</CardTitle><CardDescription>Applied automatically to every real or stock clip, then checked by you before publish.</CardDescription></CardHeader>
              <CardContent className="space-y-1 text-sm">
                {(redactions ?? []).map((r) => <div key={r.id}><Badge variant="outline">{r.kind}</Badge> scene {Number(r.scene_idx) + 1}: {r.reason}</div>)}
                <p className="text-xs text-muted-foreground">Defaults: faces of minors, victims in distress, bystanders and witnesses, reflections; plates, house numbers, addresses, phone numbers, documents, badges, screens; graphic injuries are cut away. Agency redactions are kept as released.</p>
              </CardContent>
            </Card>
          </div>
          <FoiaLog projectId={id} rows={foia ?? []} suggestions={pack.footage.foia_suggestions} />
        </>
      )}
    </div>
  );
}
