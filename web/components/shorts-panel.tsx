"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Download, ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { CopyBox, Step } from "@/components/upload-kit";
import { formatSlot, fromLocalInput, toLocalInput } from "@/lib/schedule";

export type ShortView = {
  id: string; idx: number; status: string; error: string | null; title: string | null; hook: string | null;
  description: string; tags: string; mp4: string | null; srt: string | null; duration: number | null;
  slot: string | null; slotIsSaved: boolean; youtubeUrl: string | null; aiCount: number;
  cover: string | null; coverDownload: string | null; kind: "from_video" | "animated";
};

function ShortCard({ s, tz, longTitle, longPosted, projectId, busyProject }: {
  s: ShortView; tz: string; longTitle: string; longPosted: boolean; projectId: string; busyProject: boolean;
}) {
  const router = useRouter();
  const [when, setWhen] = useState(s.slot ? toLocalInput(s.slot, tz) : "");
  const [link, setLink] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const post = async (url: string, b: unknown) => {
    setBusy(true); setMsg(null);
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
    const j = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) { setMsg(j.error ?? "Something went wrong."); return false; }
    router.refresh();
    return true;
  };
  const slotDate = when ? fromLocalInput(when, tz) : null;
  const studioDate = slotDate ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(slotDate) : "";
  const studioTime = slotDate ? new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(slotDate) : "";

  if (s.status !== "succeeded") {
    return (
      <Card>
        <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
          <CardTitle className="text-base">Short {s.idx + 1}</CardTitle>
          <Badge variant={s.status === "failed" ? "danger" : "secondary"}>{s.status === "failed" ? "failed" : "being made"}</Badge>
        </CardHeader>
        {s.status === "failed" && (
          <CardContent className="space-y-2 text-sm">
            <p className="text-destructive">{s.error?.split("\n")[0]}</p>
            <Button size="sm" variant="outline" disabled={busy || busyProject} onClick={() => post(s.kind === "animated" ? `/api/projects/${projectId}/animated` : `/api/projects/${projectId}/shorts`, s.kind === "animated" ? {} : { idx: [s.idx] })}>
              <RefreshCw className="mr-1 h-3.5 w-3.5" />Try again</Button>
          </CardContent>
        )}
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">Short {s.idx + 1}: {s.title}</CardTitle>
          {s.youtubeUrl ? <Badge variant="success">posted</Badge> : s.slotIsSaved ? <Badge variant="secondary">slot saved</Badge> : null}
        </div>
        <CardDescription>{s.duration ? `${Math.round(s.duration)} s` : ""}{s.aiCount ? ` · ${s.aiCount} AI illustration${s.aiCount > 1 ? "s" : ""} (marked on screen)` : ""}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-5 md:grid-cols-[220px_1fr]">
        <div className="space-y-2">
          {s.mp4 && <video src={s.mp4} poster={s.cover ?? undefined} controls playsInline preload="metadata" className="aspect-[9/16] w-full rounded-md bg-black" />}
          {s.cover && (
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Cover (thumbnail)</p>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={s.cover} alt="Short cover" className="aspect-[9/16] w-24 rounded border object-cover" />
            </div>
          )}
          <Button size="sm" variant="ghost" className="w-full" disabled={busy || busyProject}
            onClick={() => { if (confirm("Write a new script and make this Short again?")) post(s.kind === "animated" ? `/api/projects/${projectId}/animated` : `/api/projects/${projectId}/shorts`, s.kind === "animated" ? {} : { idx: [s.idx] }); }}>
            <RefreshCw className="mr-1 h-3.5 w-3.5" />Make again</Button>
        </div>
        <div className="min-w-0 space-y-4 text-sm">
          {s.youtubeUrl && <p>On YouTube: <a className="underline" href={s.youtubeUrl} target="_blank" rel="noreferrer">{s.youtubeUrl}</a></p>}
          <Step n={1} title="Download">
            <div className="flex flex-wrap gap-2">
              {s.mp4 && <Button size="sm" variant="outline" asChild><a href={s.mp4}><Download className="mr-1 h-4 w-4" />Short (MP4)</a></Button>}
              {s.srt && <Button size="sm" variant="outline" asChild><a href={s.srt}><Download className="mr-1 h-4 w-4" />Captions (SRT)</a></Button>}
              {s.coverDownload && <Button size="sm" variant="outline" asChild><a href={s.coverDownload}><Download className="mr-1 h-4 w-4" />Cover (JPG)</a></Button>}
            </div>
          </Step>
          <Step n={2} title="Upload in YouTube Studio">
            <p>Create → Upload videos → the MP4. YouTube makes it a Short automatically (vertical, under 3 minutes).</p>
            <CopyBox label={`Title (${(s.title ?? "").length}/100)`} text={s.title ?? ""} />
            <CopyBox label="Description" text={s.description} rows={6} />
            <p><b>Thumbnail:</b> the cover is built into the very first frame. In the thumbnail options choose the first frame
              (on the phone app: Edit thumbnail → drag to the start). If your Studio shows <b>Upload thumbnail</b> for Shorts
              (Partner Program channels), upload the cover JPG instead.</p>
            <p><b>Audience:</b> not made for kids. <b>Altered content:</b> Yes ({s.kind === "animated" ? "AI-animated scenes and AI voice" : `AI voice${s.aiCount ? " and AI illustrations" : ""}`}).</p>
            {s.kind !== "animated" && (
              <p><b>Related video</b> (Details page, below the description): choose <b>{longTitle}</b>, so a link to the full documentary shows on the Short.
                {!longPosted && " Upload the documentary first so you can pick it."}</p>
            )}
            <CopyBox label="Tags" text={s.tags} />
            <p><b>Subtitles:</b> Add → Upload file → With timing → the captions file.</p>
          </Step>
          <Step n={3} title="Schedule">
            <div className="flex flex-wrap items-end gap-2">
              <div className="space-y-1"><Label htmlFor={`w${s.id}`}>Date and time ({tz})</Label>
                <Input id={`w${s.id}`} type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></div>
              <Button size="sm" disabled={busy || !slotDate} onClick={() => post(`/api/shorts/${s.id}`, { planned_at: slotDate!.toISOString() })}>
                {s.slotIsSaved ? "Update slot" : "Save this slot"}</Button>
            </div>
            {slotDate && <p>{formatSlot(slotDate, tz)}. In Studio choose <b>Schedule</b> → <b>{studioDate}</b> at <b>{studioTime}</b>.</p>}
          </Step>
          <Step n={4} title="Paste the Short's link">
            <div className="flex flex-wrap gap-2">
              <Input className="min-w-56 flex-1" placeholder="https://youtube.com/shorts/…" value={link} onChange={(e) => setLink(e.target.value)} />
              <Button size="sm" disabled={busy || !link} onClick={async () => { if (await post(`/api/shorts/${s.id}`, { youtube_url: link, ...(slotDate ? { planned_at: slotDate.toISOString() } : {}) })) setLink(""); }}>Save link</Button>
            </div>
          </Step>
          {msg && <p className="text-destructive">{msg}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

export function ShortsPanel(props: {
  projectId: string; tz: string; shorts: ShortView[]; longTitle: string; longPosted: boolean; busy: boolean; animated?: boolean;
}) {
  return (
    <div className="space-y-3">
      {props.shorts.map((s) => (
        <ShortCard key={s.id} s={s} tz={props.tz} longTitle={props.longTitle} longPosted={props.longPosted}
          projectId={props.projectId} busyProject={props.busy} />
      ))}
      {!props.animated && (
        <p className="text-xs text-muted-foreground">
          <ExternalLink className="mr-1 inline h-3 w-3" />Shorts are planned for the days without a long video, after this documentary comes out, so each one sends viewers to it.
        </p>
      )}
    </div>
  );
}
