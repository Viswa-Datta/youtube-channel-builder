"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";

/** Name and look of an Animated Shorts channel's recurring main character. */
export function CharacterEditor({ channelId, name, look }: { channelId: string; name: string; look: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name, look });
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!open) return <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>Change character</Button>;
  return (
    <div className="space-y-2 text-sm">
      <div className="space-y-1"><Label htmlFor="cn">Name</Label><Input id="cn" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
      <div className="space-y-1"><Label htmlFor="cl">Look (face, hair, clothes, colours)</Label>
        <Textarea id="cl" rows={4} value={f.look} onChange={(e) => setF({ ...f, look: e.target.value })} /></div>
      <p className="text-xs text-muted-foreground">The next Short draws the new character once; every Short after that keeps him. Use an original character, not someone else&apos;s.</p>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={async () => {
          setBusy(true); setMsg(null);
          const r = await fetch(`/api/channels/${channelId}/character`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
          const j = await r.json().catch(() => ({}));
          setBusy(false);
          if (!r.ok) return setMsg(j.error ?? "Could not save");
          setOpen(false); router.refresh();
        }}>Save</Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
      {msg && <p className="text-destructive">{msg}</p>}
    </div>
  );
}
