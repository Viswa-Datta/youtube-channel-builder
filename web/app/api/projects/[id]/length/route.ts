import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { supabaseAdmin } from "@/lib/supabase/server";

const Req = z.object({ target_minutes: z.union([z.literal(8), z.literal(12), z.literal(20), z.literal(30), z.literal(45), z.literal(60)]) });

// Change a video's length at any time except while it is being rendered (cancel first). If narration was already
// written, it is cleared so the next render rewrites it from the scene drafts at the new length.
export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  const p = await owned<{ id: string; status: string; target_minutes: number }>(sb, "projects", id);
  const b = await body(req, Req);
  if (p.status === "rendering") throw new HttpError(409, "This video is being made right now. Cancel it first, then change the length.");
  if (b.target_minutes === p.target_minutes) return NextResponse.json({ ok: true });
  const admin = supabaseAdmin();
  const { error } = await admin.from("projects").update({ target_minutes: b.target_minutes, approved_at: null }).eq("id", id);
  if (error) throw error;
  const { error: e2 } = await admin.from("scenes").update({ narration: "", graphics: null }).eq("project_id", id).neq("narration", "");
  if (e2) throw e2;
  return NextResponse.json({ ok: true });
});
