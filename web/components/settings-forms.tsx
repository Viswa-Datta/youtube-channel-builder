"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";

export function CapForm({ cap, tier }: { cap: number; tier: string }) {
  const router = useRouter();
  const [c, setC] = useState(cap);
  const [t, setT] = useState(tier);
  const [ok, setOk] = useState(false);
  return (
    <form className="grid gap-3 sm:grid-cols-3" onSubmit={async (e) => {
      e.preventDefault();
      const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ monthly_cap_usd: c, default_tier: t }) });
      setOk(r.ok);
      router.refresh();
    }}>
      <div className="space-y-1"><Label htmlFor="cap">Monthly hard cap (USD)</Label><Input id="cap" type="number" min={0} step={0.5} value={c} onChange={(e) => setC(Number(e.target.value))} /></div>
      <div className="space-y-1"><Label htmlFor="tier">Default quality</Label>
        <Select id="tier" value={t} onChange={(e) => setT(e.target.value)}><option value="free">Free</option><option value="standard">Standard</option><option value="premium">Premium</option></Select></div>
      <div className="flex items-end"><Button type="submit">Save</Button>{ok && <span className="ml-2 text-xs text-success">Saved</span>}</div>
    </form>
  );
}

export function YoutubeConnection({ connected }: { connected: boolean }) {
  const router = useRouter();
  return connected ? (
    <Button variant="destructive" onClick={async () => { await fetch("/api/youtube/disconnect", { method: "POST" }); router.refresh(); }}>
      Disconnect and revoke access</Button>
  ) : (
    <Button asChild><a href="/api/youtube/connect">Connect YouTube</a></Button>
  );
}

export function DeleteData() {
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const go = async (delete_account: boolean) => {
    const r = await fetch("/api/account/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ delete_account, confirm }) });
    setDone(r.ok ? (delete_account ? "Your account and all data are being deleted." : "Your media and Google tokens are being deleted.") : "Type DELETE to confirm.");
  };
  return (
    <div className="space-y-2">
      <Input placeholder="Type DELETE to confirm" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => go(false)}>Delete media and Google data</Button>
        <Button variant="destructive" onClick={() => go(true)}>Delete my account</Button>
      </div>
      {done && <p className="text-sm">{done}</p>}
    </div>
  );
}
