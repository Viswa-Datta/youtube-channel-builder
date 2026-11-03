"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { CostModal, type CostContext } from "@/components/cost-modal";
import type { Tier } from "@/lib/pricing-core";

/** Niche intake -> channel row -> (cost modal) -> Channel Agent job. */
export function IntakeForm({ ctx, defaultTier }: { ctx: CostContext; defaultTier: Tier }) {
  const router = useRouter();
  const [f, setF] = useState({ niche: "", sub_niche: "", audience: "", language: "en", tone: "", target_minutes: 12, faceless: true, competitor_urls: "" });
  const [channelId, setChannelId] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));

  return (
    <form
      className="space-y-4"
      onSubmit={async (e) => {
        e.preventDefault();
        if (saving) return;
        setErr(null);
        if (channelId) return setOpen(true); // already saved: never create a duplicate channel
        setSaving(true);
        try {
          const r = await fetch("/api/channels", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...f, competitor_urls: f.competitor_urls.split(/\s+/).filter(Boolean) }),
          });
          const j = await r.json();
          if (!r.ok) return setErr(j.error ?? "Could not save");
          setChannelId(j.id);
          setOpen(true);
        } finally {
          setSaving(false);
        }
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5"><Label htmlFor="niche">Niche</Label><Input id="niche" required value={f.niche} onChange={(e) => set("niche", e.target.value)} placeholder="True Crime Documentary" /></div>
        <div className="space-y-1.5"><Label htmlFor="sub">Sub-niche</Label><Input id="sub" value={f.sub_niche} onChange={(e) => set("sub_niche", e.target.value)} placeholder="Cold cases and investigations" /></div>
        <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="aud">Audience</Label><Input id="aud" value={f.audience} onChange={(e) => set("audience", e.target.value)} placeholder="Adults who like long-form documentaries" /></div>
        <div className="space-y-1.5"><Label htmlFor="lang">Language</Label><Select id="lang" value={f.language} onChange={(e) => set("language", e.target.value)}><option value="en">English</option><option value="es">Spanish</option><option value="fr">French</option><option value="de">German</option><option value="pt">Portuguese</option></Select></div>
        <div className="space-y-1.5"><Label htmlFor="len">Target length</Label><Select id="len" value={f.target_minutes} onChange={(e) => set("target_minutes", Number(e.target.value))}>{[8, 12, 20, 30].map((m) => <option key={m} value={m}>{m} minutes</option>)}</Select></div>
        <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="tone">Tone</Label><Input id="tone" value={f.tone} onChange={(e) => set("tone", e.target.value)} placeholder="Calm, serious, long-form documentary narration" /></div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.faceless} onChange={(e) => set("faceless", e.target.checked)} /> Faceless channel (no presenter on camera)</label>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="comp">Competitor URLs (optional, style reference only)</Label>
          <Textarea id="comp" value={f.competitor_urls} onChange={(e) => set("competitor_urls", e.target.value)} placeholder="One per line. Used for tone and structure only; their cases, scripts, titles and footage are never copied." />
        </div>
      </div>
      {err && <p className="text-sm text-destructive">{err}</p>}
      <Button type="submit" disabled={saving}>{saving ? "Saving…" : "Save and generate channel kit"}</Button>
      {channelId && (
        <CostModal open={open} onOpenChange={setOpen} action="channel_kit" minutes={f.target_minutes} title="Channel kit" defaultTier={defaultTier} ctx={ctx}
          onConfirm={async (tier) => {
            const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: "channel_kit", channel_id: channelId, tier }) });
            const j = await r.json();
            if (!r.ok) throw new Error(j.error);
            router.push(`/channels/${channelId}/kit?job=${j.job.id}`);
          }} />
      )}
    </form>
  );
}
