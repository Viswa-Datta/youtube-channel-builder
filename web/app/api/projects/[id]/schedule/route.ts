import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";

// Upload kit: save the planned publish time, or record the YouTube link once the video is uploaded/scheduled.
const Req = z.object({
  render_id: z.string().uuid(),
  planned_at: z.string().datetime().optional(),
  youtube_url: z.string().url().optional(),
});

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  const p = await owned<{ id: string; idea_id: string | null }>(sb, "projects", id);
  const b = await body(req, Req);
  let url: string | null = null;
  if (b.youtube_url) {
    const m = b.youtube_url.match(/(?:youtube\.com\/(?:watch\?v=|shorts\/|live\/)|youtu\.be\/|studio\.youtube\.com\/video\/)([\w-]{11})/);
    if (!m) throw new HttpError(400, "That doesn't look like a YouTube video link. Copy it from YouTube Studio (Content → ⋮ → Get shareable link).");
    url = `https://www.youtube.com/watch?v=${m[1]}`;
  }
  const row: Record<string, unknown> = { project_id: id, render_id: b.render_id, user_id: user.id };
  if (b.planned_at) row.planned_at = b.planned_at;
  if (url) Object.assign(row, { youtube_url: url, youtube_video_id: url.slice(-11), posted_at: new Date().toISOString(), status: "uploaded" });
  let { data: existing } = await sb.from("publishes").select("id,status").eq("render_id", b.render_id).maybeSingle();
  if (!existing) {
    // Upload-kit rows start as "manual" (the user uploads in YouTube Studio); the link marks them uploaded.
    const ins = await sb.from("publishes").insert({ project_id: id, render_id: b.render_id, user_id: user.id, status: "manual",
      privacy: "public", planned_at: b.planned_at ?? null }).select("id,status").single();
    if (ins.error) throw ins.error;
    existing = ins.data;
  }
  const { error } = await sb.from("publishes").update(row).eq("id", existing.id);
  if (error) throw error;
  if (url) {
    await sb.from("projects").update({ status: "published" }).eq("id", id);
    if (p.idea_id) await sb.from("ideas").update({ status: "published" }).eq("id", p.idea_id);
  }
  return NextResponse.json({ ok: true, youtube_url: url });
});
