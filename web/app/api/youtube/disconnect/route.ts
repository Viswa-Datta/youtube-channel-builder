import { NextResponse } from "next/server";
import { apiUser, handler } from "@/lib/api";
import { decrypt } from "@/lib/crypto";
import { supabaseAdmin } from "@/lib/supabase/server";

// Revoke at Google, then delete the stored tokens.
export const POST = handler(async () => {
  const { user } = await apiUser();
  const admin = supabaseAdmin();
  const { data } = await admin.from("youtube_connections").select("refresh_token_enc").eq("user_id", user.id).maybeSingle();
  if (data?.refresh_token_enc) {
    try {
      await fetch(`https://oauth2.googleapis.com/revoke?token=${encodeURIComponent(decrypt(data.refresh_token_enc))}`, { method: "POST" });
    } catch {
      /* deletion below still happens */
    }
  }
  await admin.from("youtube_connections").delete().eq("user_id", user.id);
  return NextResponse.json({ ok: true });
});
