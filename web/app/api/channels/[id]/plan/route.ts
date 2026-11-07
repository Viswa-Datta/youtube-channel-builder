import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";

const Req = z.object({
  days: z.array(z.enum(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])).min(1).max(7),
  time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
  tz: z.string().min(3).max(64),
  youtube_url: z.string().url().nullable().optional(),
});

// The channel's publishing plan: which weekdays and what time (in the audience's time zone), plus its YouTube page.
export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  await owned(sb, "channels", id);
  const b = await body(req, Req);
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: b.tz });
  } catch {
    throw new HttpError(400, "Unknown time zone");
  }
  if (b.youtube_url && !/^https:\/\/(www\.)?youtube\.com\/(@[\w.-]+|channel\/[\w-]+|c\/[\w.-]+)\/?$/.test(b.youtube_url))
    throw new HttpError(400, "Use your channel page link, like https://www.youtube.com/@yourhandle");
  const { error } = await sb.from("channels").update({ publish_days: b.days, publish_time: b.time, publish_tz: b.tz,
    ...(b.youtube_url !== undefined ? { youtube_url: b.youtube_url } : {}) }).eq("id", id);
  if (error) throw error;
  return NextResponse.json({ ok: true });
});
