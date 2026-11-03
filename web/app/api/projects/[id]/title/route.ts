import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned } from "@/lib/api";

const Req = z.object({
  selected_title: z.string().min(1).max(59).optional(),
  description: z.string().max(5000).optional(),
  tags: z.array(z.string().max(60)).max(30).optional(),
});

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  await owned(sb, "projects", id);
  const b = await body(req, Req);
  const { error } = await sb.from("projects").update({ ...b, approved_at: null }).eq("id", id);
  if (error) throw error;
  return NextResponse.json({ ok: true });
});
