import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { enqueueJob } from "@/lib/jobs";
import type { Tier } from "@/lib/pricing";

// Make Shorts for a finished documentary: new scripts from its research, voiced and cut vertically by the worker.
// `idx` remakes specific Shorts (same slot, new script); otherwise `n` new ones are added.
const Req = z.object({
  tier: z.enum(["free", "standard", "premium"]).optional(),
  n: z.number().int().min(1).max(5).optional(),
  idx: z.array(z.number().int().min(0).max(50)).max(5).optional(),
});

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  const p = await owned<{ id: string; tier: Tier }>(sb, "projects", id);
  const b = await body(req, Req);
  const { data: render } = await sb.from("renders").select("id").eq("project_id", id).eq("status", "succeeded")
    .order("version", { ascending: false }).limit(1).maybeSingle();
  if (!render) throw new HttpError(409, "Finish the documentary first: Shorts are made from it.");
  const { count: active } = await sb.from("jobs").select("id", { count: "exact", head: true }).eq("project_id", id)
    .eq("kind", "shorts").in("status", ["queued", "running"]);
  if (active) throw new HttpError(409, "Shorts for this video are already being made.");
  const n = b.idx?.length ?? b.n ?? 2;
  const { job } = await enqueueJob({
    userId: user.id, kind: "shorts", payload: { n, ...(b.idx ? { idx: b.idx } : {}) }, action: "shorts",
    tier: b.tier ?? p.tier, minutes: n, projectId: id, renderId: render.id,
  });
  return NextResponse.json({ job });
});
