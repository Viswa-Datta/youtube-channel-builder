"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";

type Item = { key: string; label: string; pass: boolean; detail: string };

export function PublishPanel(props: {
  projectId: string; renderId: string; checklist: { pass: boolean; items: Item[] }; connected: { title: string; audited: boolean } | null;
  existing: { status: string; youtube_url: string | null; privacy: string; error: string | null } | null; mp4: string | null; pack: string | null;
  quota: { usedToday: number; dailyCap: number };
}) {
  const router = useRouter();
  const [privacy, setPrivacy] = useState("private");
  const [when, setWhen] = useState("");
  const [synthetic, setSynthetic] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const audited = props.connected?.audited ?? false;

  const submit = async (mode: "youtube" | "manual") => {
    setErr(null);
    const r = await fetch(`/api/projects/${props.projectId}/publish`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ render_id: props.renderId, mode, privacy: audited ? privacy : "private",
        publish_at: when ? new Date(when).toISOString() : null, contains_synthetic_media: synthetic }),
    });
    const j = await r.json();
    if (!r.ok) setErr(j.error);
    else router.refresh();
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>Pre-publish checklist</CardTitle><CardDescription>Publishing stays blocked until every item passes. The database enforces it too.</CardDescription></CardHeader>
        <CardContent className="space-y-1.5 text-sm">
          {props.checklist.items.map((i) => (
            <div key={i.key} className="flex items-start gap-2">
              {i.pass ? <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" /> : <X className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />}
              <span>{i.label}{i.detail && <span className="block text-xs text-muted-foreground">{i.detail}</span>}</span>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Publish</CardTitle>
          <CardDescription>
            {props.connected ? `Connected: ${props.connected.title}` : "YouTube not connected (Settings)."}
            {props.connected && !audited && " This Google Cloud project has not passed YouTube's API audit, so uploads are private-only. Download the MP4 to upload publicly by hand, or make it public in YouTube Studio after upload."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {props.existing ? (
            <div className="rounded-md border p-2">
              Status: <b>{props.existing.status}</b> · {props.existing.privacy}
              {props.existing.youtube_url && <div><a className="underline" href={props.existing.youtube_url} target="_blank" rel="noreferrer">{props.existing.youtube_url}</a></div>}
              {props.existing.error && <div className="text-xs text-destructive">{props.existing.error}</div>}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1"><Label htmlFor="priv">Privacy</Label>
                  <Select id="priv" value={audited ? privacy : "private"} disabled={!audited} onChange={(e) => setPrivacy(e.target.value)}>
                    <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option>
                  </Select></div>
                <div className="space-y-1"><Label htmlFor="when">Schedule (optional)</Label>
                  <Input id="when" type="datetime-local" disabled={!audited} value={when} onChange={(e) => setWhen(e.target.value)} /></div>
              </div>
              <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />
                Declare altered or synthetic content to YouTube (AI narration). Check YouTube&apos;s current disclosure rules.</label>
              <p className="text-xs text-muted-foreground">YouTube API quota today: {props.quota.usedToday} uploads of {props.quota.dailyCap} allowed per day. Uploads are not unlimited.</p>
              <Button className="w-full" disabled={!props.checklist.pass || !props.connected || props.quota.usedToday >= props.quota.dailyCap} onClick={() => submit("youtube")}>
                Upload to YouTube{audited ? "" : " (private)"}</Button>
            </>
          )}
          <div className="grid grid-cols-2 gap-2">
            {props.mp4 && <Button variant="outline" asChild><a href={props.mp4}>Download MP4</a></Button>}
            {props.pack && <Button variant="outline" asChild><a href={props.pack}>Upload pack .txt</a></Button>}
          </div>
          {err && <p className="text-sm text-destructive">{err}</p>}
          <p className="text-xs text-muted-foreground">You are responsible for your channel&apos;s compliance with YouTube&apos;s Terms of Service and Community Guidelines.</p>
        </CardContent>
      </Card>
    </div>
  );
}
