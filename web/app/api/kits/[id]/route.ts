import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned } from "@/lib/api";

// Save the user's picks from the channel kit (name, handle, pillars...).
const Req = z.object({ selected: z.record(z.unknown()) });

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  const kit = await owned<{ channel_id: string }>(sb, "channel_kits", id);
  const b = await body(req, Req);
  const { error } = await sb.from("channel_kits").update({ selected: b.selected }).eq("id", id);
  if (error) throw error;
  if (typeof b.selected.name === "string") await sb.from("channels").update({ name: b.selected.name }).eq("id", kit.channel_id);
  return NextResponse.json({ ok: true });
});
