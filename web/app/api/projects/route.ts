import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { llmForTier, ttsForTier, plansConfig } from "@/lib/pricing";
import { enqueueJob } from "@/lib/jobs";

// Human gate #1: a project only exists because the user picked an idea.
const Pick = z.object({ idea_id: z.string().uuid(), tier: z.enum(["free", "standard", "premium"]) });

export const POST = handler(async (req: Request) => {
  const { sb, user } = await apiUser();
  const b = await body(req, Pick);
  const idea = await owned<{ id: string; channel_id: string; title: string }>(sb, "ideas", b.idea_id);
  const { data: ch } = await sb.from("channels").select("target_minutes").eq("id", idea.channel_id).single();
  const { data: profile } = await sb.from("users").select("plan").eq("id", user.id).single();
  const plan = plansConfig.plans.find((p) => p.key === profile?.plan) ?? plansConfig.plans[0];
  const start = new Date();
  start.setDate(1);
  start.setHours(0, 0, 0, 0);
  const { count } = await sb.from("projects").select("id", { count: "exact", head: true }).gte("created_at", start.toISOString());
  if ((count ?? 0) >= plan.videos_per_month) throw new HttpError(403, `Your ${plan.name} plan includes ${plan.videos_per_month} videos per month.`);
  const { data, error } = await sb
    .from("projects")
    .insert({
      user_id: user.id, channel_id: idea.channel_id, idea_id: idea.id, title: idea.title, tier: b.tier,
      target_minutes: ch?.target_minutes ?? 12, ...llmForTier(b.tier), ...ttsForTier(b.tier),
    })
    .select("id")
    .single();
  if (error) throw error;
  await sb.from("ideas").update({ status: "in_production" }).eq("id", idea.id);
  // The cost modal was accepted for the production pack, so start it now.
  const { job } = await enqueueJob({ userId: user.id, kind: "production_pack", payload: { scenes: 7 }, action: "production_pack",
    tier: b.tier, minutes: ch?.target_minutes ?? 12, projectId: data.id, idempotencyKey: `pack:${data.id}` });
  return NextResponse.json({ id: data.id, job });
});
