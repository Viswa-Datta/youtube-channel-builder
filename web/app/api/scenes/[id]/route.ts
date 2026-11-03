import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned } from "@/lib/api";

// Editing narration changes the scene's narration_hash at the next render, so only this scene's
// voice and visuals are rebuilt; unchanged scenes are reused.
const Req = z.object({ narration: z.string().min(20).max(20000).optional(), chapter_title: z.string().max(60).optional() });

export const PATCH = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  const scene = await owned<{ project_id: string }>(sb, "scenes", id);
  const b = await body(req, Req);
  const { error } = await sb.from("scenes").update({ ...b, updated_at: new Date().toISOString() }).eq("id", id);
  if (error) throw error;
  await sb.from("projects").update({ approved_at: null, fact_check_signed_at: null }).eq("id", scene.project_id);
  return NextResponse.json({ ok: true, rerender_needed: true });
});
