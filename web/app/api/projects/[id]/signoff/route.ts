import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned } from "@/lib/api";

// Human gates: blur pass sign-off, fact-check sign-off, final approval. The DB enforces ordering
// (approval needs a blur sign-off on the latest render; publishing needs the full checklist).
const Req = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("blur"), render_id: z.string().uuid(), frames_checked: z.number().int().positive(),
    scrubbed_full_video: z.literal(true), notes: z.string().max(2000).optional() }),
  z.object({ kind: z.literal("factcheck") }),
  z.object({ kind: z.literal("approve") }),
]);

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  await owned(sb, "projects", id);
  const b = await body(req, Req);
  if (b.kind === "blur") {
    const { error } = await sb.from("blur_signoffs").upsert({ project_id: id, render_id: b.render_id, user_id: user.id,
      frames_checked: b.frames_checked, scrubbed_full_video: true, notes: b.notes ?? null }, { onConflict: "render_id" });
    if (error) throw error;
    await sb.from("scenes").update({ blur_status: "signed_off" }).eq("project_id", id).neq("blur_status", "not_needed");
  } else if (b.kind === "factcheck") {
    const { error } = await sb.from("projects").update({ fact_check_signed_at: new Date().toISOString(), fact_check_signed_by: user.id }).eq("id", id);
    if (error) throw error;
  } else {
    const { error } = await sb.from("projects").update({ approved_at: new Date().toISOString(), approved_by: user.id, status: "approved" }).eq("id", id);
    if (error) throw error;
  }
  return NextResponse.json({ ok: true });
});
