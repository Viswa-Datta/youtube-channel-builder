"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw, Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CostModal, type CostContext } from "@/components/cost-modal";
import type { Tier } from "@/lib/pricing-core";

type Kit = {
  names: { name: string; rationale: string }[]; handles: string[]; description: string; about: string; pillars: string[];
  cadence: string; banner_prompt: string; profile_image_prompt: string; trailer_idea: string;
};

/** Pick, save and regenerate any single item of the channel kit. */
export function KitEditor(props: { channelId: string; kitId: string; kit: Kit; selected: Record<string, unknown>; ctx: CostContext; defaultTier: Tier; minutes: number }) {
  const router = useRouter();
  const [item, setItem] = useState<string | null>(null);
  const [sel, setSel] = useState(props.selected as { name?: string; handle?: string });
  const k = props.kit;

  const save = async (patch: Record<string, unknown>) => {
    const next = { ...sel, ...patch };
    setSel(next);
    await fetch(`/api/kits/${props.kitId}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selected: next }) });
  };
  const Regen = ({ name }: { name: string }) => (
    <Button size="sm" variant="ghost" onClick={() => setItem(name)} aria-label={`Regenerate ${name}`}><RefreshCw className="h-3.5 w-3.5" /> Regenerate</Button>
  );
  const CopyBtn = ({ text }: { text: string }) => (
    <Button size="sm" variant="ghost" onClick={() => navigator.clipboard.writeText(text)} aria-label="Copy"><Copy className="h-3.5 w-3.5" /></Button>
  );
  const Block = ({ title, name, children, copy }: { title: string; name: string; children: React.ReactNode; copy?: string }) => (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-sm">{title}</CardTitle>
        <div className="flex">{copy && <CopyBtn text={copy} />}<Regen name={name} /></div>
      </CardHeader>
      <CardContent className="text-sm">{children}</CardContent>
    </Card>
  );

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Block title="Channel name (pick one)" name="names">
        <ul className="space-y-1.5">
          {k.names.map((n) => (
            <li key={n.name}>
              <button onClick={() => save({ name: n.name })} className={`flex w-full items-start gap-2 rounded-md border p-2 text-left ${sel.name === n.name ? "border-accent bg-accent/10" : ""}`}>
                {sel.name === n.name && <Check className="mt-0.5 h-4 w-4 shrink-0" />}
                <span><span className="font-medium">{n.name}</span><span className="block text-xs text-muted-foreground">{n.rationale}</span></span>
              </button>
            </li>
          ))}
        </ul>
      </Block>
      <Block title="Handle (pick one)" name="handles">
        <div className="flex flex-wrap gap-2">
          {k.handles.map((h) => (
            <button key={h} onClick={() => save({ handle: h })} className={`rounded-full border px-3 py-1 text-sm ${sel.handle === h ? "border-accent bg-accent/10" : ""}`}>{h}</button>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">Check availability on YouTube when you set the handle; we cannot reserve it for you.</p>
      </Block>
      <Block title="Channel description" name="description" copy={k.description}><p>{k.description}</p></Block>
      <Block title="About text" name="about" copy={k.about}><p className="whitespace-pre-line">{k.about}</p></Block>
      <Block title="Content pillars" name="pillars"><ul className="list-disc pl-5">{k.pillars.map((p) => <li key={p}>{p}</li>)}</ul></Block>
      <Block title="Posting cadence" name="cadence"><p>{k.cadence}</p></Block>
      <Block title="Banner image prompt" name="banner_prompt" copy={k.banner_prompt}><p>{k.banner_prompt}</p></Block>
      <Block title="Profile image prompt" name="profile_image_prompt" copy={k.profile_image_prompt}><p>{k.profile_image_prompt}</p></Block>
      <Block title="Trailer idea" name="trailer_idea"><p>{k.trailer_idea}</p></Block>
      {item && (
        <CostModal open={!!item} onOpenChange={(o) => !o && setItem(null)} action="channel_kit_item" minutes={props.minutes} title={`Regenerate ${item}`}
          defaultTier={props.defaultTier} ctx={props.ctx}
          onConfirm={async (tier) => {
            const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ kind: "channel_kit_item", channel_id: props.channelId, kit_id: props.kitId, item, tier }) });
            const j = await r.json();
            if (!r.ok) throw new Error(j.error);
            router.push(`?job=${j.job.id}`);
          }} />
      )}
    </div>
  );
}
