import { requireUser } from "@/lib/supabase/server";
import { estimate, plansConfig, pricing } from "@/lib/pricing";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { usd } from "@/lib/utils";

export const metadata = { title: "Cost and subscriptions" };

/** Subscription Map (what each step can use, free vs paid) + Spend Dashboard (from the cost ledger). */
export default async function Costs() {
  const { sb } = await requireUser();
  const since = new Date(new Date().getFullYear(), new Date().getMonth() - 5, 1).toISOString();
  const [{ data: ledger }, { data: profile }] = await Promise.all([
    sb.from("cost_ledger").select("provider,model,stage,cost_usd,units_in,units_out,unit_type,created_at,project_id").gte("created_at", since).order("created_at", { ascending: false }),
    sb.from("users").select("*").single(),
  ]);
  const rows = ledger ?? [];
  const byMonth = new Map<string, number>();
  const byProvider = new Map<string, number>();
  for (const r of rows) {
    const m = r.created_at.slice(0, 7);
    byMonth.set(m, (byMonth.get(m) ?? 0) + Number(r.cost_usd));
    byProvider.set(r.provider, (byProvider.get(r.provider) ?? 0) + Number(r.cost_usd));
  }
  const thisMonth = byMonth.get(new Date().toISOString().slice(0, 7)) ?? 0;
  const maxMonth = Math.max(0.01, ...byMonth.values());

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <h1 className="font-serif text-2xl">Cost and subscriptions</h1>

      <Card>
        <CardHeader><CardTitle>Per-video cost by tier</CardTitle><CardDescription>Same estimator as the cost modal. Prices verified {pricing.last_verified}.</CardDescription></CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full min-w-[480px] text-sm">
            <thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1">Length</th><th>Free</th><th>Standard</th><th>Premium</th><th>Voice characters</th></tr></thead>
            <tbody>{[8, 10, 12, 20, 30].map((m) => (
              <tr key={m} className="border-t"><td className="py-1.5">{m} min</td>
                <td>{usd(estimate("full", "free", m).total_usd)}</td><td>{usd(estimate("full", "standard", m).total_usd)}</td>
                <td>{usd(estimate("full", "premium", m).total_usd)}</td><td className="tabular-nums">{estimate("full", "free", m).voice_characters.toLocaleString()}</td></tr>
            ))}</tbody>
          </table>
          <p className="mt-2 text-xs text-muted-foreground">Rule: if a feature pushes Standard above {usd(pricing.guardrail.standard_max_usd_per_10_min)} per 10 minutes it moves to Premium. AI video clips are not included in any tier.</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Subscription map</CardTitle><CardDescription>Every paid step has a free path shown beside it.</CardDescription></CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1">Step</th><th>Free path</th><th>Paid options</th></tr></thead>
            <tbody>
              <tr className="border-t align-top"><td className="py-2">Agents (LLM)</td><td>{pricing.llm["gemini:gemini-3.1-flash-lite"].label}; or {pricing.llm["ollama:llama3.1:8b"].label}</td>
                <td>{Object.entries(pricing.llm).filter(([, m]) => (m as { paid: boolean }).paid).map(([k, m]) => <div key={k}>{(m as { label: string }).label}: ${(m as { in_per_m: number }).in_per_m}/${(m as { out_per_m: number }).out_per_m} per 1M tokens</div>)}</td></tr>
              <tr className="border-t align-top"><td className="py-2">Voice</td><td>{pricing.tts["kokoro:am_michael"].label}</td>
                <td>{Object.entries(pricing.tts).filter(([, v]) => (v as { paid: boolean }).paid).map(([k, v]) => <div key={k}>{(v as { label: string }).label}: ${(v as { per_m_chars: number }).per_m_chars}/1M chars <span className="text-xs text-muted-foreground">({(v as { commercial_rights: string }).commercial_rights})</span></div>)}</td></tr>
              <tr className="border-t align-top"><td className="py-2">Footage</td><td>Internet Archive, Library of Congress, NARA, agency releases; Pexels and Pixabay for generic B-roll</td><td>Licensed archive footage (per clip, outside the app)</td></tr>
              <tr className="border-t align-top"><td className="py-2">Music</td><td>YouTube Audio Library (download, then upload to the project) or the generated ambient bed</td><td>—</td></tr>
              <tr className="border-t align-top"><td className="py-2">Rendering</td><td>{pricing.compute.self_hosted.label}</td><td>{pricing.compute.fly_shared_4x.label}: ${pricing.compute.fly_shared_4x.per_render_hour}/render-hour</td></tr>
              <tr className="border-t align-top"><td className="py-2">Publishing</td><td>YouTube Data API v3 (free, quota-limited)</td><td>—</td></tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Spend dashboard</CardTitle><CardDescription>This month {usd(thisMonth)} of {usd(profile?.monthly_cap_usd)} hard cap.</CardDescription></CardHeader>
          <CardContent className="space-y-1.5">
            {[...byMonth.entries()].sort().map(([m, v]) => (
              <div key={m} className="flex items-center gap-2 text-sm"><span className="w-16 tabular-nums">{m}</span>
                <div className="h-3 flex-1 rounded bg-secondary"><div className="h-3 rounded bg-accent" style={{ width: `${(v / maxMonth) * 100}%` }} /></div>
                <span className="w-20 text-right tabular-nums">{usd(v, 4)}</span></div>
            ))}
            {!byMonth.size && <p className="text-sm text-muted-foreground">No spend yet.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Cost by provider (6 months)</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm">
            {[...byProvider.entries()].sort((a, b) => b[1] - a[1]).map(([p, v]) => <div key={p} className="flex justify-between"><span>{p}</span><span className="tabular-nums">{usd(v, 4)}</span></div>)}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Ledger</CardTitle><CardDescription>Every agent and provider call: model, units, cost.</CardDescription></CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-xs">
            <thead className="text-left text-muted-foreground"><tr><th className="py-1">When</th><th>Stage</th><th>Provider</th><th>Model</th><th className="text-right">Units</th><th className="text-right">Cost</th></tr></thead>
            <tbody>{rows.slice(0, 100).map((r, i) => (
              <tr key={i} className="border-t"><td className="py-1">{new Date(r.created_at).toLocaleString()}</td><td>{r.stage}</td><td>{r.provider}</td><td>{r.model}</td>
                <td className="text-right tabular-nums">{(Number(r.units_in) + Number(r.units_out)).toLocaleString()} {r.unit_type}</td><td className="text-right tabular-nums">{usd(r.cost_usd, 4)}</td></tr>
            ))}</tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Plans</CardTitle><CardDescription>Configured in config/plans.json. Usage add-ons are priced at cost plus {Math.round(plansConfig.addons.margin * 100)}% and shown before purchase.</CardDescription></CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          {plansConfig.plans.map((pl) => (
            <div key={pl.key} className={`rounded-md border p-3 ${profile?.plan === pl.key ? "border-accent" : ""}`}>
              <div className="flex items-center justify-between"><b>{pl.name}</b>{profile?.plan === pl.key && <Badge>Current</Badge>}</div>
              <div className="text-xl">{usd(pl.price_month, 0)}<span className="text-xs text-muted-foreground">/month</span></div>
              <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">
                <li>{pl.channels} channel(s), {pl.videos_per_month} videos/month</li>
                <li>Tiers: {pl.allowed_tiers.join(", ")}</li>{pl.features.map((f) => <li key={f}>{f}</li>)}
              </ul>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
