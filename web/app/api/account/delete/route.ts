import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler } from "@/lib/api";
import { supabaseAdmin } from "@/lib/supabase/server";

// Data deletion path: connected Google data (tokens) is revoked and deleted, media is removed,
// and with delete_account the user's rows are deleted (cascade) and the auth user removed.
const Req = z.object({ delete_account: z.boolean(), confirm: z.literal("DELETE") });

export const POST = handler(async (req: Request) => {
  const { user } = await apiUser();
  const b = await body(req, Req);
  const admin = supabaseAdmin();
  await admin.from("jobs").insert({ user_id: user.id, kind: "delete_user_data", payload: { user_id: user.id, delete_account: b.delete_account },
    idempotency_key: `delete:${user.id}:${new Date().toISOString().slice(0, 10)}`, priority: 1 });
  // Google tokens are revoked by the worker job; the auth user (and every row, by cascade) is deleted after media.
  if (b.delete_account) await (await import("@/lib/supabase/server")).supabaseServer().then((sb) => sb.auth.signOut());
  return NextResponse.json({ ok: true, queued: true });
});
