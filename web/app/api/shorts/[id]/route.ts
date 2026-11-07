import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";

// Shorts upload kit: save the planned slot, or record the YouTube link once the Short is posted/scheduled.
const Req = z.object({
  planned_at: z.string().datetime().nullable().optional(),
  youtube_url: z.string().url().optional(),
});

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  await owned(sb, "shorts", id);
  const b = await body(req, Req);
  const row: Record<string, unknown> = { updated_at: new Date().toISOString() };
  if (b.planned_at !== undefined) row.planned_at = b.planned_at;
  if (b.youtube_url) {
    const m = b.youtube_url.match(/(?:youtube\.com\/(?:watch\?v=|shorts\/|live\/)|youtu\.be\/|studio\.youtube\.com\/video\/)([\w-]{11})/);
    if (!m) throw new HttpError(400, "That doesn't look like a YouTube link. Copy it from YouTube Studio (Content → Shorts → ⋮ → Get shareable link).");
    Object.assign(row, { youtube_url: `https://www.youtube.com/shorts/${m[1]}`, posted_at: new Date().toISOString() });
  }
  const { error } = await sb.from("shorts").update(row).eq("id", id);
  if (error) throw error;
  return NextResponse.json({ ok: true, youtube_url: row.youtube_url ?? null });
});
