import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { enqueueJob } from "@/lib/jobs";
import { llmForTier, plansConfig, type Tier } from "@/lib/pricing";

// One endpoint for every agent job. The client only gets here after the cost modal was accepted.
const Req = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("channel_kit"), tier: z.enum(["free", "standard", "premium"]), channel_id: z.string().uuid() }),
  z.object({ kind: z.literal("channel_kit_item"), tier: z.enum(["free", "standard", "premium"]), channel_id: z.string().uuid(), kit_id: z.string().uuid(),
    item: z.enum(["names", "handles", "description", "about", "pillars", "cadence", "banner_prompt", "profile_image_prompt", "trailer_idea"]) }),
  z.object({ kind: z.literal("ideas"), tier: z.enum(["free", "standard", "premium"]), channel_id: z.string().uuid(), count: z.number().int().min(20).max(30).default(25) }),
  z.object({ kind: z.literal("production_pack"), tier: z.enum(["free", "standard", "premium"]), project_id: z.string().uuid(), scenes: z.number().int().min(3).max(16).optional() }),
]);

export const POST = handler(async (req: Request) => {
  const { sb, user } = await apiUser();
  const b = await body(req, Req);
  const { data: profile } = await sb.from("users").select("plan").eq("id", user.id).single();
  const plan = plansConfig.plans.find((p) => p.key === profile?.plan) ?? plansConfig.plans[0];
  if (!plan.allowed_tiers.includes(b.tier)) throw new HttpError(403, `${b.tier} quality is not included in the ${plan.name} plan.`);
  const llm = llmForTier(b.tier as Tier);

  if (b.kind === "production_pack") {
    const p = await owned<{ id: string; target_minutes: number }>(sb, "projects", b.project_id);
    const { job, estimate } = await enqueueJob({ userId: user.id, kind: b.kind, payload: { scenes: b.scenes }, action: "production_pack",
      tier: b.tier, minutes: p.target_minutes, projectId: p.id, idempotencyKey: `pack:${p.id}:${Date.now()}` });
    return NextResponse.json({ job, estimate });
  }
  const ch = await owned<{ id: string; target_minutes: number }>(sb, "channels", b.channel_id);
  const action = b.kind === "channel_kit_item" ? "channel_kit_item" : b.kind;
  const payload: Record<string, unknown> = { channel_id: ch.id, tier: b.tier, ...llm };
  if (b.kind === "channel_kit_item") Object.assign(payload, { kit_id: b.kit_id, item: b.item });
  if (b.kind === "ideas") payload.count = b.count;
  const { job, estimate } = await enqueueJob({ userId: user.id, kind: b.kind, payload, action, tier: b.tier, minutes: ch.target_minutes });
  return NextResponse.json({ job, estimate });
});
