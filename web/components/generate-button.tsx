"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, type ButtonProps } from "@/components/ui/button";
import { CostModal, type CostContext } from "@/components/cost-modal";
import type { Action, Tier } from "@/lib/pricing-core";

/** Any "generate" action: opens the cost modal first, then calls the endpoint with the chosen tier. */
export function GenerateButton(props: {
  label: string;
  action: Action;
  minutes: number;
  defaultTier: Tier;
  ctx: CostContext;
  endpoint: string;
  payload: Record<string, unknown>;
  onStarted?: (res: { job?: { id: string }; render?: { id: string }; id?: string }) => void;
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
  /** Path to open after the job starts; "{job}" is replaced with the new job id. A string, not a function, so server pages can pass it. */
  redirectTo?: string;
}) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  return (
    <>
      <Button variant={props.variant} size={props.size} onClick={() => setOpen(true)}>{props.label}</Button>
      <CostModal
        open={open}
        onOpenChange={setOpen}
        action={props.action}
        minutes={props.minutes}
        title={props.label}
        defaultTier={props.defaultTier}
        ctx={props.ctx}
        onConfirm={async (tier) => {
          const r = await fetch(props.endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...props.payload, tier }) });
          const j = await r.json();
          if (!r.ok) throw new Error(j.error ?? "failed");
          props.onStarted?.(j);
          const jobId = (j.job as { id?: string } | undefined)?.id;
          const to = props.redirectTo && jobId ? props.redirectTo.replace("{job}", jobId) : null;
          if (to) router.push(to);
          else router.refresh();
        }}
      />
    </>
  );
}
