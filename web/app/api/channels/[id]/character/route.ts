import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned } from "@/lib/api";

// The Animated Shorts channel's main character. Changing the name or look clears the reference picture: the next
// Short draws the new character once and keeps him for every Short after that.
const Req = z.object({ name: z.string().min(1).max(40), look: z.string().min(20).max(600) });

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  await owned(sb, "channels", id);
  const b = await body(req, Req);
  const { error } = await sb.from("channels").update({ character: { name: b.name.trim(), look: b.look.trim() } }).eq("id", id);
  if (error) throw error;
  return NextResponse.json({ ok: true });
});
