import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned } from "@/lib/api";

const Move = z.object({
  status: z.enum(["suggested", "shortlisted", "in_production", "ready", "published", "dismissed"]).optional(),
  position: z.number().int().min(0).optional(),
});

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb } = await apiUser();
  await owned(sb, "ideas", id);
  const b = await body(req, Move);
  const { error } = await sb.from("ideas").update(b).eq("id", id);
  if (error) throw error;
  return NextResponse.json({ ok: true });
});
