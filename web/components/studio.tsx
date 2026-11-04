"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Play, Pause, Captions, CaptionsOff, SkipBack, SkipForward, Square } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { ts } from "@/lib/utils";

export type StudioScene = {
  id: string; idx: number; title: string; chapter_title: string | null; narration: string; start_sec: number; audio_duration_sec: number;
  footage_type: string | null; blur_status: string; word_count: number;
  assets: StudioAsset[];
};
export type StudioAsset = {
  id: string; kind: string; tier: string; title: string | null; source_name: string | null; agency: string | null; release_date: string | null;
  source_url: string | null; license: string; rights_checked: boolean; verified_real: boolean; is_broll_label: boolean; selected: boolean;
  meta: Record<string, unknown>;
};
type Claim = { id: string; claim: string; status: string; source_refs: string[] };

const VOICES: Record<string, { id: string; label: string }[]> = {
  kokoro: [{ id: "am_michael", label: "Michael (US, calm)" }, { id: "bm_george", label: "George (UK, measured)" }, { id: "af_heart", label: "Heart (US)" }, { id: "bf_emma", label: "Emma (UK)" }],
  openai: [{ id: "onyx", label: "Onyx" }, { id: "echo", label: "Echo" }, { id: "fable", label: "Fable" }, { id: "nova", label: "Nova" }],
  elevenlabs: [{ id: "pNInz6obpgDQGcFmaJgB", label: "Adam" }],
  google: [{ id: "en-US-Chirp3-HD-Charon", label: "Charon (Chirp 3 HD)" }],
};

export function Studio(props: {
  projectId: string; renderId: string; video: string; vtt: string; duration: number; scenes: StudioScene[]; claims: Claim[];
  titles: string[]; selectedTitle: string | null; thumbnails: { id: string; url: string | null; selected: boolean }[];
  downloads: { label: string; url: string | null }[]; voice: { provider: string; id: string; speed: number };
  signoff: { blur: boolean; factcheck: boolean; approved: boolean };
}) {
  const router = useRouter();
  const v = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [cc, setCc] = useState(true);
  const [rate, setRate] = useState(1);
  const [cue, setCue] = useState("");
  const [voice, setVoice] = useState(props.voice);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // blur review: which 5-second buckets the reviewer has looked at, and how many frames were stepped/checked
  const buckets = Math.max(1, Math.ceil(props.duration / 5));
  const [seen, setSeen] = useState<Set<number>>(new Set());
  const [framesChecked, setFramesChecked] = useState(0);
  const [drawing, setDrawing] = useState(false);
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);

  const scene = useMemo(() => [...props.scenes].reverse().find((s) => t >= Number(s.start_sec)) ?? props.scenes[0], [t, props.scenes]);

  useEffect(() => {
    const el = v.current;
    if (!el) return;
    const tr = el.textTracks[0];
    if (tr) {
      tr.mode = cc ? "showing" : "hidden";
      tr.oncuechange = () => setCue(((tr.activeCues?.[0] as VTTCue | undefined)?.text ?? "").replace(/\n/g, " "));
    }
  }, [cc]);
  useEffect(() => {
    if (v.current) v.current.playbackRate = rate;
  }, [rate]);

  const seek = (s: number) => {
    if (!v.current) return;
    v.current.currentTime = Math.max(0, Math.min(props.duration, s));
    setSeen((x) => new Set(x).add(Math.floor(v.current!.currentTime / 5)));
    setFramesChecked((n) => n + 1);
  };
  const post = async (url: string, body: unknown) => {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    setMsg(r.ok ? "Saved." : j.error);
    if (r.ok) router.refresh();
    return r.ok;
  };
  const coverage = seen.size / buckets;

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
      <div className="min-w-0 space-y-3">
        <div className="relative overflow-hidden rounded-lg bg-black">
          <video ref={v} src={props.video} className="aspect-video w-full" crossOrigin="anonymous" preload="metadata" playsInline
            onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
            onTimeUpdate={(e) => { const c = e.currentTarget.currentTime; setT(c); setSeen((x) => (x.has(Math.floor(c / 5)) ? x : new Set(x).add(Math.floor(c / 5)))); }}>
            <track kind="captions" src={props.vtt} srcLang="en" label="English" default />
          </video>
          {drawing && (
            <div className="absolute inset-0 cursor-crosshair"
              onPointerDown={(e) => { const r = e.currentTarget.getBoundingClientRect(); start.current = { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }; setBox(null); }}
              onPointerMove={(e) => { if (!start.current) return; const r = e.currentTarget.getBoundingClientRect(); const x = (e.clientX - r.left) / r.width; const y = (e.clientY - r.top) / r.height;
                setBox({ x: Math.min(x, start.current.x), y: Math.min(y, start.current.y), w: Math.abs(x - start.current.x), h: Math.abs(y - start.current.y) }); }}
              onPointerUp={() => { start.current = null; }}>
              {box && <div className="absolute border-2 border-accent bg-accent/20" style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }} />}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="icon" onClick={() => (playing ? v.current?.pause() : v.current?.play())} aria-label={playing ? "Pause" : "Play"}>{playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}</Button>
          <Button size="icon" variant="outline" onClick={() => seek(t - 1 / 30)} aria-label="Previous frame"><SkipBack className="h-4 w-4" /></Button>
          <Button size="icon" variant="outline" onClick={() => seek(t + 1 / 30)} aria-label="Next frame"><SkipForward className="h-4 w-4" /></Button>
          <span className="text-xs tabular-nums text-muted-foreground">{ts(t)} / {ts(props.duration)}</span>
          <Button size="sm" variant="outline" onClick={() => setCc(!cc)} aria-pressed={cc}>{cc ? <Captions className="h-4 w-4" /> : <CaptionsOff className="h-4 w-4" />} Captions {cc ? "on" : "off"}</Button>
          <Select className="h-8 w-24" value={rate} onChange={(e) => setRate(Number(e.target.value))} aria-label="Playback speed">
            {[0.75, 1, 1.25, 1.5, 2].map((r) => <option key={r} value={r}>{r}×</option>)}
          </Select>
        </div>
        <div className="flex gap-1 overflow-x-auto pb-1">
          {props.scenes.map((s) => (
            <Button key={s.id} size="sm" variant={scene?.id === s.id ? "default" : "outline"} className="shrink-0" onClick={() => seek(Number(s.start_sec))}>
              {ts(Number(s.start_sec))} {s.chapter_title ?? s.title}
            </Button>
          ))}
        </div>
        <div className="min-h-[2.5rem] rounded-md border bg-card p-2 text-sm"><span className="text-xs text-muted-foreground">Caption preview: </span>{cue}</div>

        <Card>
          <CardHeader><CardTitle className="text-base">Scene {scene.idx + 1}: {scene.title}</CardTitle>
            <CardDescription>{scene.word_count} words · {ts(Number(scene.audio_duration_sec))} · edit the narration and re-render: only changed scenes are rebuilt.</CardDescription></CardHeader>
          <CardContent className="space-y-2">
            <SceneEditor key={scene.id} scene={scene} onSaved={() => setDirty(true)} />
            <ClipSwap scene={scene} post={post} />
          </CardContent>
        </Card>
      </div>

      <div className="min-w-0 space-y-3">
        {msg && <p className="rounded-md border bg-card p-2 text-sm">{msg}</p>}
        <Card>
          <CardHeader><CardTitle className="text-base">Scenes</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {props.scenes.map((s) => (
              <button key={s.id} onClick={() => seek(Number(s.start_sec))} className={`flex w-full flex-wrap items-center gap-1 rounded-md p-1.5 text-left text-xs hover:bg-muted ${scene.id === s.id ? "bg-muted" : ""}`}>
                <span className="mr-auto font-medium">{s.idx + 1}. {s.title}</span>
                <Badge variant={s.footage_type === "real" ? "default" : s.footage_type === "stock_broll" ? "outline" : "secondary"}>
                  {s.footage_type === "stock_broll" ? "B-roll" : s.footage_type === "real" ? "Real" : s.footage_type === "news_document" ? "News/doc" : "Graphics"}</Badge>
                {s.assets.some((a) => a.tier === "gap") && <Badge variant="danger">Gap</Badge>}
                <Badge variant={s.blur_status === "signed_off" ? "success" : s.blur_status === "not_needed" ? "secondary" : "warning"}>Blur: {s.blur_status.replace("_", " ")}</Badge>
              </button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Voice</CardTitle><CardDescription>Changing voice or speed re-voices every scene at the next render.</CardDescription></CardHeader>
          <CardContent className="grid grid-cols-2 gap-2">
            <Select value={voice.provider} onChange={(e) => { const p = e.target.value; setVoice({ ...voice, provider: p, id: VOICES[p][0].id }); setDirty(true); }} aria-label="Voice provider">
              <option value="kokoro">Kokoro (free)</option><option value="openai">OpenAI</option><option value="elevenlabs">ElevenLabs</option><option value="google">Google Chirp 3</option>
            </Select>
            <Select value={voice.id} onChange={(e) => { setVoice({ ...voice, id: e.target.value }); setDirty(true); }} aria-label="Voice">
              {VOICES[voice.provider].map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
            </Select>
            <Label className="col-span-2 text-xs">Speed {voice.speed.toFixed(2)}×
              <input type="range" min={0.8} max={1.2} step={0.05} value={voice.speed} className="w-full" onChange={(e) => { setVoice({ ...voice, speed: Number(e.target.value) }); setDirty(true); }} />
            </Label>
            {dirty && (
              <Button className="col-span-2" variant="accent" onClick={() => post(`/api/projects/${props.projectId}/render`, { voice_provider: voice.provider, voice_id: voice.id, voice_speed: voice.speed })
                .then((ok) => ok && router.push(`/projects/${props.projectId}`))}>Re-render changed scenes</Button>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Title and thumbnail</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            {props.titles.map((title) => (
              <label key={title} className="flex items-start gap-2">
                <input type="radio" name="title" defaultChecked={title === props.selectedTitle} onChange={() => post(`/api/projects/${props.projectId}/title`, { selected_title: title })} />
                <span>{title} <span className="text-xs text-muted-foreground">({title.length}/60)</span></span>
              </label>
            ))}
            <div className="grid grid-cols-2 gap-2">
              {props.thumbnails.map((th) => (
                <button key={th.id} onClick={() => post(`/api/assets/${th.id}`, { action: "select" })} className={`overflow-hidden rounded-md border-2 ${th.selected ? "border-accent" : "border-transparent"}`}>
                  {th.url && <img src={th.url} alt="Thumbnail option" className="aspect-video w-full object-cover" />}
                </button>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Blur pass (required)</CardTitle>
            <CardDescription>Scrub the whole video. Pause, draw a box over anything missed, then sign off. Blur is burned into the pixels.</CardDescription></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Progress value={coverage * 100} />
            <p className="text-xs text-muted-foreground">Reviewed {seen.size}/{buckets} five-second sections · {framesChecked} frames stepped</p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => { v.current?.pause(); setDrawing(!drawing); setBox(null); }}><Square className="h-3.5 w-3.5" /> {drawing ? "Cancel box" : "Draw blur box"}</Button>
              {box && box.w > 0.01 && (
                <Button size="sm" onClick={async () => {
                  const ok = await post("/api/redactions", { project_id: props.projectId, scene_idx: scene.idx, kind: "manual_box", t_start: Math.max(0, t - 0.5), t_end: t + 3, box, reason: "reviewer" });
                  if (ok) { setDrawing(false); setBox(null); setDirty(true); }
                }}>Save box ({ts(t)}–{ts(t + 3)})</Button>
              )}
            </div>
            <Button className="w-full" disabled={props.signoff.blur || coverage < 1 || framesChecked < 1}
              onClick={() => post(`/api/projects/${props.projectId}/signoff`, { kind: "blur", render_id: props.renderId, frames_checked: Math.max(framesChecked, seen.size), scrubbed_full_video: true })}>
              {props.signoff.blur ? "Blur pass signed off" : coverage < 1 ? `Scrub the rest of the video (${Math.round(coverage * 100)}%)` : "Sign off blur pass"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Fact-check</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-xs">
            {props.claims.map((c) => (
              <div key={c.id} className="flex gap-1.5"><Badge variant={c.status === "confirmed" ? "success" : c.status === "disputed" ? "warning" : "danger"}>{c.status}</Badge><span>{c.claim}</span></div>
            ))}
            <Button className="mt-2 w-full" variant="outline" disabled={props.signoff.factcheck} onClick={() => post(`/api/projects/${props.projectId}/signoff`, { kind: "factcheck" })}>
              {props.signoff.factcheck ? "Fact-check signed" : "I checked every claim against its source"}</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Approve and download</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <Button className="w-full" disabled={props.signoff.approved || !props.signoff.blur} onClick={() => post(`/api/projects/${props.projectId}/signoff`, { kind: "approve" })}>
              {props.signoff.approved ? "Approved" : "Approve final video"}</Button>
            <div className="grid grid-cols-2 gap-2">
              {props.downloads.map((d) => d.url && <Button key={d.label} size="sm" variant="outline" asChild><a href={d.url}>{d.label}</a></Button>)}
            </div>
            <Button className="w-full" variant="secondary" asChild><a href={`/projects/${props.projectId}/publish`}>Go to Publish →</a></Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function SceneEditor({ scene, onSaved }: { scene: StudioScene; onSaved: () => void }) {
  const [text, setText] = useState(scene.narration);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  return (
    <div className="space-y-1">
      <Textarea rows={8} value={text} onChange={(e) => { setText(e.target.value); setState("idle"); }} aria-label="Scene narration" />
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={text === scene.narration || state === "saving"} onClick={async () => {
          setState("saving");
          const r = await fetch(`/api/scenes/${scene.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ narration: text }) });
          setState(r.ok ? "saved" : "idle");
          if (r.ok) onSaved();
        }}>Save narration</Button>
        <span className="text-xs text-muted-foreground">{text.split(/\s+/).filter(Boolean).length} words {state === "saved" && "· saved, re-render to hear it"}</span>
      </div>
    </div>
  );
}

function ClipSwap({ scene, post }: { scene: StudioScene; post: (u: string, b: unknown) => Promise<boolean> }) {
  const [check, setCheck] = useState<StudioAsset | null>(null);
  const [f, setF] = useState({ license: "Public Domain", agency: "", release_date: "", source_url: "", rights_notes: "", verified_real: true, confirm: false });
  const clips = scene.assets.filter((a) => a.kind === "clip" || a.tier === "gap");
  if (!clips.length) return <p className="text-xs text-muted-foreground">No candidate clips for this scene. Visuals are document and chapter cards made for this video.</p>;
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium">Clips for this scene</div>
      {clips.map((a) => (
        <div key={a.id} className="flex flex-wrap items-center gap-1.5 rounded border p-1.5 text-xs">
          <Badge variant={a.tier === "real" ? "default" : a.tier === "gap" ? "danger" : "outline"}>{a.tier === "stock_broll" ? "B-roll" : a.tier}</Badge>
          <span className="mr-auto min-w-0 truncate">{a.title}</span>
          {a.tier === "gap" ? <span className="text-muted-foreground">search “{String(a.meta.search_query ?? "")}” · {String(a.meta.source_hint ?? "")}</span> : (
            <>
              <Badge variant={a.license === "Do Not Use" ? "danger" : "secondary"}>{String(a.meta?.license_short ?? a.license)}</Badge>
              {Boolean(a.meta?.auto_rights) && <Badge variant="outline" title="Licence read from the source's own metadata; open the source to double-check">auto-checked</Badge>}
              {a.verified_real && <Badge variant="success">Verified real</Badge>}
              {a.source_url && <a className="underline" href={a.source_url} target="_blank" rel="noreferrer">source</a>}
              {(a.tier === "real" || a.tier === "news_document") && !a.rights_checked && <Button size="sm" variant="outline" onClick={() => setCheck(a)}>Rights check</Button>}
              <Button size="sm" variant={a.selected ? "default" : "outline"} onClick={() => post(`/api/assets/${a.id}`, { action: a.selected ? "unselect" : "select" })}>{a.selected ? "Using" : "Use"}</Button>
            </>
          )}
        </div>
      ))}
      {check && (
        <form className="grid gap-2 rounded-md border p-2 sm:grid-cols-2" onSubmit={async (e) => {
          e.preventDefault();
          const ok = await post(`/api/assets/${check.id}`, { action: "rights_check", license: f.license, agency: f.agency, release_date: f.release_date,
            source_url: f.source_url, rights_notes: f.rights_notes, verified_real: f.verified_real && check.tier === "real", confirm_not_sealed_or_leaked: f.confirm });
          if (ok) setCheck(null);
        }}>
          <div className="text-xs font-medium sm:col-span-2">Rights check: {check.title}. Publicly available does not mean free to reuse; state and local releases vary.</div>
          <Select value={f.license} onChange={(e) => setF({ ...f, license: e.target.value })}>
            {["Public Domain", "Public Record with release terms", "Licensed", "Commentary-only", "Do Not Use"].map((l) => <option key={l}>{l}</option>)}
          </Select>
          <Input required placeholder="Original agency or publisher" value={f.agency} onChange={(e) => setF({ ...f, agency: e.target.value })} />
          <Input required type="date" value={f.release_date} onChange={(e) => setF({ ...f, release_date: e.target.value })} />
          <Input required type="url" placeholder="Original release URL (not a re-upload)" value={f.source_url} onChange={(e) => setF({ ...f, source_url: e.target.value })} />
          <Textarea required className="sm:col-span-2" placeholder="Where the release terms are stated and what they allow" value={f.rights_notes} onChange={(e) => setF({ ...f, rights_notes: e.target.value })} />
          {check.tier === "real" && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.verified_real} onChange={(e) => setF({ ...f, verified_real: e.target.checked })} /> Show as verified real footage</label>}
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" required checked={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.checked })} /> Not sealed, juvenile, leaked or unreleased</label>
          <Button type="submit" size="sm" className="sm:col-span-2">Save rights check</Button>
        </form>
      )}
    </div>
  );
}
