"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, type ButtonProps } from "@/components/ui/button";

/** Stops a video that is being made. Asks once, because the work done so far on this render is dropped. */
export function CancelRenderButton({ renderId, size = "sm" }: { renderId: string; size?: ButtonProps["size"] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <Button variant="outline" size={size} disabled={busy}
      className="border-destructive/50 text-destructive hover:bg-destructive/10"
      onClick={async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!window.confirm("Cancel this video? It stops within about a minute. You can render it again later.")) return;
        setBusy(true);
        const r = await fetch(`/api/renders/${renderId}/cancel`, { method: "POST" });
        setBusy(false);
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          window.alert(j.error ?? "Could not cancel.");
        }
        router.refresh();
      }}>
      {busy ? "Cancelling…" : "Cancel video"}
    </Button>
  );
}
