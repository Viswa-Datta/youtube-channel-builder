import { NextResponse } from "next/server";
import { apiUser, handler, owned, HttpError } from "@/lib/api";
import { supabaseAdmin } from "@/lib/supabase/server";

// Cancel a video that is being made. The worker notices within about a minute and stops; nothing more is spent on it.
// The project goes back to "pack ready", so it can be rendered again later.
export const POST = handler(async (_req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  const r = await owned<{ id: string; project_id: string; status: string }>(sb, "renders", id);
  if (!["queued", "running", "waiting_user", "paused_cap"].includes(r.status)) throw new HttpError(409, "This video is not being made right now.");
  const admin = supabaseAdmin();
  const now = new Date().toISOString();
  const { error: e1 } = await admin.from("jobs").update({ status: "cancelled", finished_at: now, last_error: "Cancelled by you" })
    .eq("render_id", id).in("status", ["queued", "running", "paused_cap"]);
  if (e1) throw e1;
  const { error: e2 } = await admin.from("renders").update({ status: "cancelled", error: "Cancelled by you", finished_at: now }).eq("id", id);
  if (e2) throw e2;
  await sb.from("help_requests").update({ status: "skipped", resolved_at: now }).eq("project_id", r.project_id).eq("status", "open");
  await admin.from("projects").update({ status: "pack_ready" }).eq("id", r.project_id).eq("status", "rendering");
  return NextResponse.json({ ok: true });
});
