import { wakeWorker } from "@/lib/fly";
import { NextResponse } from "next/server";
import { apiUser, handler, owned, HttpError } from "@/lib/api";
import { supabaseAdmin } from "@/lib/supabase/server";

// Footage Desk -> "Continue": resume a render paused for the user. Not a new estimate: the user already accepted
// the whole-video cost when the render started. Open help requests are marked skipped (their scenes use graphics).
export const POST = handler(async (_req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  const r = await owned<{ id: string; project_id: string; status: string }>(sb, "renders", id);
  if (r.status !== "waiting_user") throw new HttpError(409, "This render is not waiting for you.");
  await sb.from("help_requests").update({ status: "skipped", resolved_at: new Date().toISOString() }).eq("project_id", r.project_id).eq("status", "open");
  const admin = supabaseAdmin();
  const { error: e1 } = await admin.from("renders").update({ status: "queued", stage: "blur" }).eq("id", id).eq("status", "waiting_user");
  if (e1) throw e1;
  const key = `render:${id}:blur`;
  const { data: job, error } = await admin.from("jobs").upsert(
    { user_id: user.id, kind: "render_stage", stage: "blur", payload: {}, project_id: r.project_id, render_id: id, idempotency_key: key, priority: 50 },
    { onConflict: "idempotency_key", ignoreDuplicates: true }).select().maybeSingle();
  if (error) throw error;
  const j = job ?? (await admin.from("jobs").select("*").eq("idempotency_key", key).single()).data;
  await wakeWorker();
  return NextResponse.json({ ok: true, job: j });
});
