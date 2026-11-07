import { wakeWorker } from "@/lib/fly";
import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { supabaseAdmin } from "@/lib/supabase/server";

const Req = z.object({
  render_id: z.string().uuid(),
  mode: z.enum(["youtube", "manual"]),
  privacy: z.enum(["private", "unlisted", "public"]).default("private"),
  publish_at: z.string().datetime().nullable().optional(),
  contains_synthetic_media: z.boolean().default(true),
  category_id: z.string().regex(/^\d+$/).default("27"),
});

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  await owned(sb, "projects", id);
  const b = await body(req, Req);
  const { data: checklist } = await sb.rpc("publish_checklist", { p_project: id, p_render: b.render_id });
  if (b.mode === "youtube" && !checklist?.pass) throw new HttpError(409, "Pre-publish checklist is not complete", { checklist });
  if (b.mode === "youtube") {
    const { data: conn } = await sb.from("youtube_connection_status").select("*").maybeSingle();
    if (!conn) throw new HttpError(409, "Connect your YouTube channel in Settings first.");
  }
  // The insert trigger re-checks the checklist and the render's audio stream inside the database.
  const { data: pub, error } = await sb
    .from("publishes")
    .insert({ project_id: id, render_id: b.render_id, user_id: user.id, privacy: b.privacy, publish_at: b.publish_at ?? null,
      status: b.mode === "manual" ? "manual" : "queued" })
    .select()
    .single();
  if (error) {
    if (error.code === "23505") throw new HttpError(409, "This render was already published or queued (no double uploads).");
    throw error;
  }
  if (b.mode === "youtube") {
    await supabaseAdmin().from("jobs").insert({ user_id: user.id, project_id: id, render_id: b.render_id, kind: "publish",
      payload: { publish_id: pub.id, contains_synthetic_media: b.contains_synthetic_media, category_id: b.category_id },
      idempotency_key: `publish:${pub.id}`, max_attempts: 6 });
  }
  await wakeWorker();
  return NextResponse.json({ publish: pub, checklist });
});
