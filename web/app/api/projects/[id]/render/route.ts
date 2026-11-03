import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { startRender } from "@/lib/jobs";
import type { Tier } from "@/lib/pricing";

const Req = z.object({
  voice_provider: z.enum(["kokoro", "openai", "elevenlabs", "google"]).optional(),
  voice_id: z.string().max(80).optional(),
  voice_speed: z.number().min(0.7).max(1.3).optional(),
  caption_mode: z.enum(["burned", "sidecar", "both"]).optional(),
});

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  const p = await owned<{ id: string; status: string; tier: Tier; target_minutes: number }>(sb, "projects", id);
  if (!["pack_ready", "review", "failed", "approved"].includes(p.status)) throw new HttpError(409, `Project is ${p.status}; wait for it to finish.`);
  const b = await body(req, Req);
  if (Object.keys(b).length) await sb.from("projects").update({ ...b, approved_at: null }).eq("id", id);
  const { render, job } = await startRender(user.id, id, p.tier, p.target_minutes);
  return NextResponse.json({ render, job });
});
