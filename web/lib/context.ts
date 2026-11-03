import "server-only";
import { supabaseServer } from "./supabase/server";
import { plansConfig, type Tier } from "./pricing";

/** Data the cost modal needs: month spend, cap, allowed tiers, "videos like this cost $X last time". */
export async function costContext(minutes?: number, tier?: Tier) {
  const sb = await supabaseServer();
  const { data: u } = await sb.auth.getUser();
  const { data: profile } = await sb.from("users").select("*").eq("id", u.user!.id).single();
  const start = new Date();
  start.setDate(1);
  start.setHours(0, 0, 0, 0);
  const { data: rows } = await sb.from("cost_ledger").select("cost_usd").gte("created_at", start.toISOString());
  const monthSpend = (rows ?? []).reduce((a, r) => a + Number(r.cost_usd), 0);
  const plan = plansConfig.plans.find((p) => p.key === profile?.plan) ?? plansConfig.plans[0];
  let pastCost: { renders: number; avg: number | null } | null = null;
  if (minutes && tier) {
    const { data } = await sb.rpc("past_render_cost", { p_user: u.user!.id, p_tier: tier, p_minutes: minutes });
    const r = Array.isArray(data) ? data[0] : data;
    pastCost = r ? { renders: Number(r.renders), avg: r.avg_cost_usd === null ? null : Number(r.avg_cost_usd) } : null;
  }
  return {
    profile,
    plan,
    ctx: { monthSpend, monthlyCap: Number(profile?.monthly_cap_usd ?? 0), allowedTiers: plan.allowed_tiers as Tier[], pastCost },
    defaultTier: (profile?.default_tier ?? "standard") as Tier,
  };
}
