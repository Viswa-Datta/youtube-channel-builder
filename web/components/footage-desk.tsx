"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ExternalLink, Upload, Check, X, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export type DeskScene = { id: string; idx: number; title: string };
export type DeskRequest = {
  id: string; scene_id: string | null; kind: "save_clip" | "confirm_rights" | "find_source"; title: string; instructions: string;
  source_url: string | null; source_name: string | null; agency: string | null; release_date: string | null;
  start_sec: number | null; end_sec: number | null; license_hint: string | null; rights_note: string | null; status: string;
};
export type DeskAsset = {
  id: string; scene_id: string | null; kind: string; tier: string; title: string | null; source_name: string | null; source_url: string | null;
  agency: string | null; release_date: string | null; license: string; rights_notes: string | null; rights_checked: boolean; selected: boolean;
  in_sec: number | null; out_sec: number | null; preview: string | null; dismissed: boolean; uploaded: boolean;
};

const fmt = (s: number | null) => (s === null || s === undefined ? "" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`);
const parseTime = (v: string): number | null => {
  const t = v.trim();
  if (!t) return null;
  const parts = t.split(":").map(Number);
  if (parts.some((n) => Number.isNaN(n))) return null;
  return parts.reduce((a, n) => a * 60 + n, 0);
};
const LICENSES = ["Public Record with release terms", "Public Domain", "Licensed", "Commentary-only"];

async function post(url: string, data: unknown) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error ?? `Request failed (${r.status})`);
  return j;
}

function uploadWithProgress(url: string, file: File, onProgress: (f: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", url);
    x.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => (x.status >= 200 && x.status < 300 ? resolve() : reject(new Error(`Upload failed (${x.status}). Check the bucket CORS policy.`)));
    x.onerror = () => reject(new Error("Upload failed: the storage bucket refused the browser. Check the R2 CORS policy."));
    x.send(file);
  });
}

function UploadForm({ projectId, req, scenes, onDone }: { projectId: string; req: DeskRequest; scenes: DeskScene[]; onDone: () => void }) {
  const isVideoSite = /youtube\.com|youtu\.be|vimeo\.com/i.test(req.source_url ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState({
    title: req.title.replace(/^Find:\s*/, ""), agency: req.agency ?? "", release_date: req.release_date ?? "", source_url: req.source_url ?? "",
    license: req.license_hint ?? "Public Record with release terms", rights_notes: req.rights_note ?? "",
    scene_id: req.scene_id ?? scenes[0]?.id ?? "", start: fmt(req.start_sec), end: fmt(req.end_sec),
    official_channel: isVideoSite, not_sealed: false,
  });
  const [pct, setPct] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const input = "w-full rounded-md border bg-background px-2 py-1.5 text-sm";

  const submit = async () => {
    setErr(null);
    try {
      if (!file) throw new Error("Choose the video or photo file first.");
      if (!f.not_sealed) throw new Error("Please confirm the last box.");
      const up = await post("/api/uploads", { project_id: projectId, filename: file.name, content_type: file.type || "video/mp4", size: file.size });
      setPct(0);
      await uploadWithProgress(up.url, file, setPct);
      await post(`/api/help-requests/${req.id}`, {
        action: "complete", key: up.key, kind: (file.type || "").startsWith("image/") ? "image" : "clip", scene_id: f.scene_id,
        title: f.title, agency: f.agency, release_date: f.release_date || null, source_url: f.source_url, license: f.license,
        rights_notes: f.rights_notes.length >= 10 ? f.rights_notes : `${f.rights_notes} (checked by uploader)`.trim(),
        start_sec: parseTime(f.start), end_sec: parseTime(f.end), official_channel: f.official_channel, confirm_not_sealed_or_leaked: true,
      });
      onDone();
    } catch (e) {
      setErr((e as Error).message);
      setPct(null);
    }
  };

  return (
    <div className="mt-3 space-y-2 rounded-md border bg-muted/30 p-3 text-sm">
      <label className="block font-medium">1. Choose the file you saved
        <input type="file" accept="video/*,image/*" className="mt-1 block w-full text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="block">Scene<select className={input} value={f.scene_id} onChange={(e) => set("scene_id", e.target.value)}>
          {scenes.map((s) => <option key={s.id} value={s.id}>Scene {s.idx + 1}: {s.title}</option>)}</select></label>
        <label className="block">Title<input className={input} value={f.title} onChange={(e) => set("title", e.target.value)} /></label>
        <label className="block">Agency or court that released it<input className={input} value={f.agency} onChange={(e) => set("agency", e.target.value)} /></label>
        <label className="block">Release date (YYYY-MM-DD)<input className={input} placeholder="2021-09-15" value={f.release_date} onChange={(e) => set("release_date", e.target.value)} /></label>
        <label className="block sm:col-span-2">Link to the original release<input className={input} value={f.source_url} onChange={(e) => set("source_url", e.target.value)} /></label>
        <label className="block">Use from (m:ss, optional)<input className={input} placeholder="2:10" value={f.start} onChange={(e) => set("start", e.target.value)} /></label>
        <label className="block">Use until (m:ss, optional)<input className={input} placeholder="4:45" value={f.end} onChange={(e) => set("end", e.target.value)} /></label>
        <label className="block">License<select className={input} value={f.license} onChange={(e) => set("license", e.target.value)}>
          {LICENSES.map((l) => <option key={l}>{l}</option>)}</select></label>
        <label className="block">Rights notes<input className={input} value={f.rights_notes} onChange={(e) => set("rights_notes", e.target.value)} placeholder="e.g. Released by the agency under the state public records act" /></label>
      </div>
      {isVideoSite && (
        <label className="flex items-start gap-2"><input type="checkbox" checked={f.official_channel} onChange={(e) => set("official_channel", e.target.checked)} />
          <span>This link is the agency&apos;s own official channel (not a re-upload by someone else).</span></label>
      )}
      <label className="flex items-start gap-2"><input type="checkbox" checked={f.not_sealed} onChange={(e) => set("not_sealed", e.target.checked)} />
        <span>I checked the release terms, and this is not sealed, leaked, juvenile or unreleased material.</span></label>
      {err && <p className="text-destructive">{err}</p>}
      <Button onClick={submit} disabled={pct !== null}>
        <Upload className="h-4 w-4" /> {pct === null ? "Upload and use in the video" : `Uploading ${Math.round(pct * 100)}%`}
      </Button>
    </div>
  );
}

export function FootageDesk(props: { projectId: string; renderId: string | null; waiting: boolean; scenes: DeskScene[]; requests: DeskRequest[]; assets: DeskAsset[] }) {
  const router = useRouter();
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [assets, setAssets] = useState(props.assets);
  useEffect(() => setAssets(props.assets), [props.assets]);
  const sceneName = (id: string | null) => { const s = props.scenes.find((x) => x.id === id); return s ? `Scene ${s.idx + 1}: ${s.title}` : "Any scene"; };

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setErr(null); setBusy(key);
    try { await fn(); router.refresh(); } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const openReqs = props.requests.filter((r) => r.status === "open");
  const doneReqs = props.requests.filter((r) => r.status !== "open");
  const found = assets.filter((a) => !a.uploaded && !a.dismissed && !a.rights_checked && a.license !== "Do Not Use");
  const using = assets.filter((a) => a.selected && (a.tier === "real" || a.tier === "news_document"));

  return (
    <div className="space-y-4">
      {props.waiting && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-accent bg-accent/10 p-3 text-sm">
          <span><b>The video is paused for you.</b> {openReqs.length} help request{openReqs.length === 1 ? "" : "s"} and {found.length} found item{found.length === 1 ? "" : "s"} to review. Anything you skip is replaced by maps, documents and timeline graphics.</span>
          <Button disabled={busy === "continue"} onClick={() => act("continue", async () => {
            const j = await post(`/api/renders/${props.renderId}/continue`, {});
            router.push(`/projects/${props.projectId}?job=${j.job.id}`);
          })}><Play className="h-4 w-4" /> Continue making the video</Button>
        </div>
      )}
      {err && <p className="text-sm text-destructive">{err}</p>}

      <Card>
        <CardHeader><CardTitle className="text-base">Help needed ({openReqs.length})</CardTitle></CardHeader>
        <CardContent className="space-y-3 text-sm">
          {openReqs.length === 0 && <p className="text-muted-foreground">Nothing needs you right now.</p>}
          {openReqs.map((r) => (
            <div key={r.id} className="rounded-md border p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium">{r.title}</div>
                  <div className="text-xs text-muted-foreground">{sceneName(r.scene_id)}{r.agency ? ` · ${r.agency}` : ""}{r.release_date ? ` · ${r.release_date}` : ""}</div>
                </div>
                <Badge variant={r.kind === "save_clip" ? "warning" : "secondary"}>{r.kind === "save_clip" ? "Save this clip" : r.kind === "find_source" ? "Find a source" : "Confirm rights"}</Badge>
              </div>
              <p className="mt-2 text-muted-foreground">{r.instructions}</p>
              {r.rights_note && <p className="mt-1 text-xs text-muted-foreground">Rights: {r.rights_note}</p>}
              <div className="mt-2 flex flex-wrap gap-2">
                {r.source_url && <Button asChild size="sm" variant="outline"><a href={r.source_url} target="_blank" rel="noreferrer"><ExternalLink className="h-3.5 w-3.5" /> Open the original</a></Button>}
                <Button size="sm" onClick={() => setOpen(open === r.id ? null : r.id)}><Upload className="h-3.5 w-3.5" /> I have the file</Button>
                <Button size="sm" variant="ghost" disabled={busy === r.id} onClick={() => act(r.id, () => post(`/api/help-requests/${r.id}`, { action: "skip" }))}>Skip</Button>
              </div>
              {open === r.id && <UploadForm projectId={props.projectId} req={r} scenes={props.scenes} onDone={() => { setOpen(null); router.refresh(); }} />}
            </div>
          ))}
          {doneReqs.length > 0 && (
            <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">Finished or skipped ({doneReqs.length})</summary>
              <ul className="mt-1 space-y-1">{doneReqs.map((r) => (
                <li key={r.id} className="flex items-center gap-2">{r.status === "done" ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}{r.title}
                  {r.status === "skipped" && <button className="underline" onClick={() => act(r.id, () => post(`/api/help-requests/${r.id}`, { action: "reopen" }))}>reopen</button>}</li>))}
              </ul></details>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Found by the app ({found.length})</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-xs text-muted-foreground">These come from sources that allow downloading (Internet Archive, National Archives, Library of Congress, Wikimedia Commons). Open each one, check it is really about this case and that the license fits, then approve it.</p>
          {found.length === 0 && <p className="text-muted-foreground">Nothing waiting for review.</p>}
          {found.map((a) => (
            <div key={a.id} className="flex flex-wrap items-start gap-3 rounded-md border p-2">
              {a.preview && a.kind === "image" && <img src={a.preview} alt="" className="h-16 w-24 rounded object-cover" />}
              <div className="min-w-0 flex-1">
                <div className="font-medium">{a.title}</div>
                <div className="text-xs text-muted-foreground">{sceneName(a.scene_id)} · {a.source_name}{a.agency ? ` · ${a.agency}` : ""}{a.release_date ? ` · ${a.release_date}` : ""}</div>
                <div className="mt-1 flex flex-wrap gap-1"><Badge variant="outline">{a.kind === "image" ? "Photo/document" : "Video"}</Badge><Badge variant="secondary">{a.license}</Badge></div>
                {a.rights_notes && <p className="mt-1 text-xs text-muted-foreground">{a.rights_notes}</p>}
              </div>
              <div className="flex gap-1">
                {a.source_url && <Button asChild size="sm" variant="outline"><a href={a.source_url} target="_blank" rel="noreferrer"><ExternalLink className="h-3.5 w-3.5" /> Check</a></Button>}
                <Button size="sm" disabled={busy === a.id} onClick={() => act(a.id, () => post(`/api/assets/${a.id}`, { action: "approve_found", confirm_rights_checked: true, confirm_not_sealed_or_leaked: true }))}><Check className="h-3.5 w-3.5" /> Use it</Button>
                <Button size="sm" variant="ghost" disabled={busy === a.id} onClick={() => act(a.id, () => post(`/api/assets/${a.id}`, { action: "dismiss" }))}>Not relevant</Button>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">In the video ({using.length})</CardTitle></CardHeader>
        <CardContent className="space-y-1 text-sm">
          {using.length === 0 && <p className="text-muted-foreground">No real footage yet. Scenes without footage use maps, documents and timeline graphics.</p>}
          {using.map((a) => (
            <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded border p-2">
              <span className="min-w-0 truncate">{a.title} <span className="text-xs text-muted-foreground">· {sceneName(a.scene_id)}{a.in_sec !== null || a.out_sec !== null ? ` · ${fmt(a.in_sec) || "0:00"}–${fmt(a.out_sec) || "end"}` : ""}</span></span>
              <Button size="sm" variant="ghost" onClick={() => act(a.id, () => post(`/api/assets/${a.id}`, { action: "unselect" }))}>Remove</Button>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
