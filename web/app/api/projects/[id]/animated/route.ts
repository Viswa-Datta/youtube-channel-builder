import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { enqueueJob } from "@/lib/jobs";
import type { Tier } from "@/lib/pricing";

// Make the animated Short again (new script, new scenes). Research is kept.
const Req = z.object({ tier: z.enum(["free", "standard", "premium"]).optional() });

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  const p = await owned<{ id: string; tier: Tier; channel_id: string }>(sb, "projects", id);
  const b = await body(req, Req);
  const { data: ch } = await sb.from("channels").select("format").eq("id", p.channel_id).single();
  if (ch?.format !== "animated_shorts") throw new HttpError(400, "This is not an Animated Shorts channel.");
  const { count: active } = await sb.from("jobs").select("id", { count: "exact", head: true }).eq("project_id", id)
    .eq("kind", "animated_short").in("status", ["queued", "running"]);
  if (active) throw new HttpError(409, "This Short is already being made.");
  const { count: done } = await sb.from("jobs").select("id", { count: "exact", head: true }).eq("project_id", id).eq("kind", "animated_short");
  const version = (done ?? 0) + 1;
  const { job } = await enqueueJob({ userId: user.id, kind: "animated_short", payload: { version }, action: "animated_short",
    tier: b.tier ?? p.tier, minutes: 8, projectId: id, idempotencyKey: `anim:${id}:${version}` });
  return NextResponse.json({ job });
});
