"use client";
import { useMemo, useState } from "react";
import { estimateWith, type Action, type Tier } from "@/lib/pricing-core";
import P from "@/config/pricing.json";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { usd } from "@/lib/utils";

export interface CostContext {
  monthSpend: number;
  monthlyCap: number;
  allowedTiers: Tier[];
  pastCost?: { renders: number; avg: number | null } | null;
}

/**
 * Shown before every generation. Line items per stage, provider and plan, the free path beside every paid
 * line, a Free/Standard/Premium toggle that re-prices instantly, and the hard cap check.
 */
export const LENGTHS = [8, 12, 20, 30, 45, 60];

export function CostModal(props: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  action: Action;
  minutes: number;
  title: string;
  defaultTier: Tier;
  ctx: CostContext;
  onConfirm: (tier: Tier, minutes: number) => Promise<void>;
  /** Show a video-length picker (each video can have its own length). */
  chooseMinutes?: boolean;
}) {
  const [tier, setTier] = useState<Tier>(props.defaultTier);
  const [minutes, setMinutes] = useState<number>(props.minutes);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const est = useMemo(() => estimateWith(P, props.action, tier, minutes), [props.action, tier, minutes]);
  const all = useMemo(
    () => (["free", "standard", "premium"] as Tier[]).map((t) => [t, estimateWith(P, props.action, t, minutes).total_usd] as const),
    [props.action, minutes],
  );
  const remaining = props.ctx.monthlyCap - props.ctx.monthSpend;
  const overCap = est.total_usd > remaining + 1e-9;
  const notAllowed = !props.ctx.allowedTiers.includes(tier);

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent>
        <DialogTitle>Cost before you generate: {props.title}</DialogTitle>
        <DialogDescription>Prices verified {est.prices_last_verified}. You are charged only what the ledger records.</DialogDescription>

        {props.chooseMinutes && (
          <div className="mt-4">
            <div className="mb-1 text-sm font-medium">Video length</div>
            <div className="grid grid-cols-6 gap-1.5" role="radiogroup" aria-label="Video length">
              {LENGTHS.map((m) => (
                <button key={m} role="radio" aria-checked={minutes === m} onClick={() => setMinutes(m)}
                  className={`rounded-md border px-1 py-1.5 text-sm transition ${minutes === m ? "border-accent bg-accent/10 font-medium" : "hover:bg-muted"}`}>
                  {m} min
                </button>
              ))}
            </div>
            {minutes !== props.minutes && props.action === "whole_video" && (
              <p className="mt-1 text-xs text-muted-foreground">New length: the script is rewritten to {minutes} minutes and the voice is re-recorded. Research and footage are kept.</p>
            )}
          </div>
        )}
        <div className="mt-4 grid grid-cols-3 gap-2" role="radiogroup" aria-label="Quality tier">
          {all.map(([t, total]) => (
            <button
              key={t}
              role="radio"
              aria-checked={tier === t}
              onClick={() => setTier(t)}
              className={`rounded-md border p-2 text-left text-sm transition ${tier === t ? "border-accent bg-accent/10" : "hover:bg-muted"}`}
            >
              <div className="font-medium capitalize">{t}</div>
              <div className="text-xs text-muted-foreground">{usd(total)}</div>
            </button>
          ))}
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[520px] text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr><th className="py-1 pr-2">Stage</th><th className="pr-2">Provider / plan</th><th className="pr-2 text-right">Units</th><th className="pr-2 text-right">Cost</th><th>Free path</th></tr>
            </thead>
            <tbody>
              {est.lines.map((l) => (
                <tr key={l.stage} className="border-t align-top">
                  <td className="py-1.5 pr-2">{l.stage}</td>
                  <td className="pr-2">{l.plan}</td>
                  <td className="pr-2 text-right tabular-nums">{l.units.toLocaleString()} {l.unit_type}</td>
                  <td className="pr-2 text-right tabular-nums">{usd(l.cost_usd, 4)}</td>
                  <td className="text-xs text-muted-foreground">{l.free_alternative || (l.paid ? "" : "—")}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t font-semibold"><td className="py-2" colSpan={3}>Total</td><td className="text-right tabular-nums">{usd(est.total_usd, 4)}</td><td /></tr>
            </tfoot>
          </table>
        </div>

        {(props.action === "whole_video" || props.action === "full") && (
          <p className="mt-2 text-xs text-muted-foreground">Voice math: {est.math}. Per 10 minutes: {usd(est.per_10_min_usd)}.</p>
        )}
        {props.ctx.pastCost?.renders ? (
          <p className="mt-1 text-xs text-muted-foreground">Videos like this cost {usd(props.ctx.pastCost.avg)} last time (average of {props.ctx.pastCost.renders}).</p>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="secondary">This month: {usd(props.ctx.monthSpend)} of {usd(props.ctx.monthlyCap)} cap</Badge>
          {overCap && <Badge variant="danger">Would exceed your hard cap — generation will pause</Badge>}
          {notAllowed && <Badge variant="warning">Not in your plan</Badge>}
          {est.guardrail_violation && <Badge variant="danger">{est.guardrail_violation}</Badge>}
        </div>
        {err && <p className="mt-3 text-sm text-destructive">{err}</p>}
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={() => props.onOpenChange(false)}>Cancel</Button>
          <Button
            disabled={busy || overCap || notAllowed || !!est.guardrail_violation}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await props.onConfirm(tier, minutes);
                props.onOpenChange(false);
              } catch (e) {
                setErr((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Starting…" : `Generate for ${usd(est.total_usd)}`}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
