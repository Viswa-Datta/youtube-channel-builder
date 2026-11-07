"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, Download, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { DAYS, DAY_LABEL, formatSlot, fromLocalInput, toLocalInput } from "@/lib/schedule";

type Plan = { days: string[]; time: string; tz: string; youtube_url: string | null };

function CopyBox({ label, text, rows }: { label: string; text: string; rows?: number }) {
  const [done, setDone] = useState(false);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <Label>{label}</Label>
        <Button size="sm" variant="outline" onClick={async () => { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); }}>
          {done ? <Check className="mr-1 h-3.5 w-3.5" /> : <Copy className="mr-1 h-3.5 w-3.5" />}{done ? "Copied" : "Copy"}
        </Button>
      </div>
      {rows ? (
        <textarea readOnly rows={rows} value={text} className="w-full rounded-md border bg-muted/40 p-2 font-mono text-xs" />
      ) : (
        <div className="rounded-md border bg-muted/40 p-2 text-sm">{text || <span className="text-muted-foreground">(empty)</span>}</div>
      )}
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-semibold text-accent-foreground">{n}</div>
      <div className="min-w-0 flex-1 space-y-2"><div className="font-medium">{title}</div>{children}</div>
    </div>
  );
}

export function UploadKit(props: {
  projectId: string; renderId: string; channelId: string; channelName: string; plan: Plan;
  slot: string; slotIsSaved: boolean; youtubeUrl: string | null;
  title: string; description: string; tags: string; mp4: string | null; thumb: string | null; srt: string | null;
  watchNextCount: number; checklist: { label: string; pass: boolean; detail: string }[];
}) {
  const router = useRouter();
  const tz = props.plan.tz;
  const [when, setWhen] = useState(toLocalInput(props.slot, tz));
  const [link, setLink] = useState("");
  const [plan, setPlan] = useState<Plan>(props.plan);
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
  const slotDate = fromLocalInput(when, tz);
  const studioDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(slotDate);
  const studioTime = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(slotDate);
  const missing = props.checklist.filter((c) => !c.pass);

  return (
    <div className="space-y-4">
      {props.youtubeUrl && (
        <div className="rounded-md border border-success/40 bg-success/10 p-3 text-sm">
          Scheduled on YouTube: <a className="underline" href={props.youtubeUrl} target="_blank" rel="noreferrer">{props.youtubeUrl}</a>.
          Future videos will link to it in their description.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Release time</CardTitle>
            <CardDescription>The next free slot in your schedule. Change it if you need to.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="text-lg font-semibold">{formatSlot(slotDate, tz)}</div>
            <div className="flex flex-wrap items-end gap-2">
              <div className="space-y-1"><Label htmlFor="when">Date and time ({tz})</Label>
                <Input id="when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></div>
              <Button disabled={busy} onClick={() => post(`/api/projects/${props.projectId}/schedule`, { render_id: props.renderId, planned_at: slotDate.toISOString() })}>
                {props.slotIsSaved ? "Update slot" : "Save this slot"}</Button>
            </div>
            {!props.slotIsSaved && <p className="text-xs text-muted-foreground">Save it so the next video gets the following slot.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Channel schedule</CardTitle>
            <CardDescription>Same days and time every week. Viewers learn when to expect you.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-1">
              {DAYS.map((d) => (
                <button key={d} type="button" aria-pressed={plan.days.includes(d)}
                  onClick={() => setPlan({ ...plan, days: plan.days.includes(d) ? plan.days.filter((x) => x !== d) : [...plan.days, d] })}
                  className={`rounded-md border px-2.5 py-1 ${plan.days.includes(d) ? "border-accent bg-accent/15 font-medium" : "hover:bg-muted"}`}>{DAY_LABEL[d]}</button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1"><Label htmlFor="pt">Time</Label><Input id="pt" type="time" value={plan.time} onChange={(e) => setPlan({ ...plan, time: e.target.value })} /></div>
              <div className="space-y-1"><Label htmlFor="tz">Time zone</Label>
                <select id="tz" className="w-full rounded-md border bg-background px-2 py-2" value={plan.tz} onChange={(e) => setPlan({ ...plan, tz: e.target.value })}>
                  {["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London", "Asia/Kolkata", "Australia/Sydney", plan.tz]
                    .filter((v, i, a) => a.indexOf(v) === i).map((z) => <option key={z} value={z}>{z.replace("_", " ")}</option>)}
                </select></div>
            </div>
            <div className="space-y-1"><Label htmlFor="cu">Your channel link (for the subscribe link)</Label>
              <Input id="cu" placeholder="https://www.youtube.com/@yourhandle" value={plan.youtube_url ?? ""} onChange={(e) => setPlan({ ...plan, youtube_url: e.target.value || null })} /></div>
            <Button variant="outline" disabled={busy || !plan.days.length} onClick={() => post(`/api/channels/${props.channelId}/plan`, plan)}>Save schedule</Button>
          </CardContent>
        </Card>
      </div>

      {missing.length > 0 && (
        <div className="rounded-md border border-warning/50 bg-warning/10 p-3 text-sm">
          <div className="font-medium">Worth checking before you upload</div>
          <ul className="mt-1 list-disc pl-5 text-xs">{missing.map((m) => <li key={m.label}>{m.label}{m.detail ? `: ${m.detail}` : ""}</li>)}</ul>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Upload kit</CardTitle>
          <CardDescription>Everything for YouTube Studio, in the order Studio asks for it. About 3 minutes.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 text-sm">
          <Step n={1} title="Download the files">
            <div className="flex flex-wrap gap-2">
              {props.mp4 && <Button variant="outline" asChild><a href={props.mp4}><Download className="mr-1 h-4 w-4" />Video (MP4)</a></Button>}
              {props.thumb && <Button variant="outline" asChild><a href={props.thumb}><Download className="mr-1 h-4 w-4" />Thumbnail</a></Button>}
              {props.srt && <Button variant="outline" asChild><a href={props.srt}><Download className="mr-1 h-4 w-4" />Captions (SRT)</a></Button>}
            </div>
          </Step>
          <Step n={2} title="Start the upload">
            <p>Open YouTube Studio → <b>Create</b> → <b>Upload videos</b> → choose the MP4.</p>
            <Button variant="outline" asChild><a href="https://studio.youtube.com" target="_blank" rel="noreferrer"><ExternalLink className="mr-1 h-4 w-4" />Open YouTube Studio</a></Button>
          </Step>
          <Step n={3} title="Details page">
            <CopyBox label={`Title (${props.title.length}/100)`} text={props.title} />
            <CopyBox label={`Description${props.watchNextCount ? ` (links to ${props.watchNextCount} earlier video${props.watchNextCount > 1 ? "s" : ""})` : ""}`} text={props.description} rows={9} />
            <p><b>Thumbnail:</b> Upload file → the thumbnail you downloaded. <b>Audience:</b> &quot;No, it&apos;s not made for kids&quot;.</p>
          </Step>
          <Step n={4} title="Show more">
            <p><b>Altered content:</b> Yes (the narration is an AI voice). <b>Language:</b> English. <b>Category:</b> Education.</p>
            <CopyBox label="Tags" text={props.tags} />
          </Step>
          <Step n={5} title="Video elements">
            <p><b>Add subtitles</b> → Upload file → <i>With timing</i> → the captions file.</p>
            <p><b>Add end screen</b>: the last 20 seconds of your video already show two &quot;watch next&quot; frames and a subscribe circle.
              Add <b>Video → Best for viewer</b> on the left frame, <b>Video → Most recent upload</b> on the right frame, and <b>Subscribe</b> on the circle, then drag each onto its frame.
              From your second upload on, use <b>Import from video</b> to copy it in one click.</p>
          </Step>
          <Step n={6} title="Visibility">
            <p>Choose <b>Schedule</b> and set <b>{studioDate}</b> at <b>{studioTime}</b> (your computer&apos;s time zone), then <b>Schedule</b>.</p>
          </Step>
          <Step n={7} title="Paste the video link here">
            <p className="text-muted-foreground">In Studio: Content → ⋮ next to the video → Get shareable link. This marks the video as scheduled and lets future videos link to it.</p>
            <div className="flex flex-wrap gap-2">
              <Input className="min-w-64 flex-1" placeholder="https://youtu.be/…" value={link} onChange={(e) => setLink(e.target.value)} />
              <Button disabled={busy || !link} onClick={async () => { if (await post(`/api/projects/${props.projectId}/schedule`, { render_id: props.renderId, planned_at: slotDate.toISOString(), youtube_url: link })) setLink(""); }}>Save link</Button>
            </div>
          </Step>
          {msg && <p className="text-sm text-destructive">{msg}</p>}
          <p className="text-xs text-muted-foreground">You are responsible for your channel&apos;s compliance with YouTube&apos;s Terms of Service and Community Guidelines.</p>
        </CardContent>
      </Card>
    </div>
  );
}
