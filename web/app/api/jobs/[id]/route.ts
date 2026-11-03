import { NextResponse } from "next/server";
import { apiUser, handler, HttpError } from "@/lib/api";

export const GET = handler(async (_req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  const { data } = await sb.from("jobs").select("id,kind,stage,status,progress,progress_note,attempts,max_attempts,last_error,render_id,project_id,created_at,finished_at").eq("id", id).maybeSingle();
  if (!data) throw new HttpError(404, "job not found");
  let render = null;
  if (data.render_id) {
    const r = await sb.from("renders").select("id,status,stage,progress,error").eq("id", data.render_id).maybeSingle();
    render = r.data;
  }
  // Missing provider keys are reported verbatim so the user can fix them; nothing is faked.
  const missing = data.last_error?.match(/is not configured: set (.+)$/m)?.[1] ?? null;
  return NextResponse.json({ ...data, render, missing_keys: missing });
});
