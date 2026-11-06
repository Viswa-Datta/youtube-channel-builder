import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { startRender } from "@/lib/jobs";
import { supabaseAdmin } from "@/lib/supabase/server";
import type { Tier } from "@/lib/pricing";

const Req = z.object({
  voice_provider: z.enum(["kokoro", "openai", "elevenlabs", "google"]).optional(),
  voice_id: z.string().max(80).optional(),
  voice_speed: z.number().min(0.7).max(1.3).optional(),
  caption_mode: z.enum(["burned", "sidecar", "both"]).optional(),
  target_minutes: z.union([z.literal(8), z.literal(12), z.literal(20), z.literal(30), z.literal(45), z.literal(60)]).optional(),
});

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  const p = await owned<{ id: string; status: string; tier: Tier; target_minutes: number }>(sb, "projects", id);
  if (!["pack_ready", "review", "failed", "approved"].includes(p.status)) throw new HttpError(409, `Project is ${p.status}; wait for it to finish.`);
  const { count: active } = await sb.from("renders").select("id", { count: "exact", head: true }).eq("project_id", id)
    .in("status", ["queued", "running", "waiting_user", "paused_cap"]);
  if (active) throw new HttpError(409, "This video is already being made. Cancel it first to start again.");
  const b = await body(req, Req);
  if (b.target_minutes && b.target_minutes !== p.target_minutes) {
    // New length: the narration is rewritten from each scene's draft at the new length, then the voice is re-recorded
    // and the animations re-planned. Research, footage and the drafts are kept.
    const { error } = await supabaseAdmin().from("scenes").update({ narration: "", graphics: null }).eq("project_id", id);
    if (error) throw error;
  }
  if (Object.keys(b).length) await sb.from("projects").update({ ...b, approved_at: null }).eq("id", id);
  const { render, job } = await startRender(user.id, id, p.tier, b.target_minutes ?? p.target_minutes);
  return NextResponse.json({ render, job });
});
