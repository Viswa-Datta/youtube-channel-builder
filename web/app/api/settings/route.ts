import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler } from "@/lib/api";

const Req = z.object({
  monthly_cap_usd: z.number().min(0).max(10000).optional(),
  default_tier: z.enum(["free", "standard", "premium"]).optional(),
  display_name: z.string().max(80).optional(),
});

export const POST = handler(async (req: Request) => {
  const { sb, user } = await apiUser();
  const b = await body(req, Req);
  const { error } = await sb.from("users").update(b).eq("id", user.id);
  if (error) throw error;
  // Raising the cap releases jobs that were paused for it.
  if (b.monthly_cap_usd !== undefined) {
    const { supabaseAdmin } = await import("@/lib/supabase/server");
    await supabaseAdmin().from("jobs").update({ status: "queued", run_after: new Date().toISOString() }).eq("user_id", user.id).eq("status", "paused_cap");
  }
  return NextResponse.json({ ok: true });
});
