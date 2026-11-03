import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { apiUser, handler, HttpError } from "@/lib/api";
import { encrypt } from "@/lib/crypto";
import { exchangeCode, myChannel } from "@/lib/youtube";
import { supabaseAdmin } from "@/lib/supabase/server";

export const GET = handler(async (req: Request) => {
  const { user } = await apiUser();
  const url = new URL(req.url);
  const store = await cookies();
  const expected = store.get("yt_oauth_state")?.value;
  if (!expected || url.searchParams.get("state") !== expected) throw new HttpError(400, "OAuth state mismatch; try connecting again.");
  if (url.searchParams.get("error")) return NextResponse.redirect(new URL(`/settings?youtube=denied`, url));
  const tok = await exchangeCode(url.searchParams.get("code") ?? "");
  if (!tok.refresh_token) throw new HttpError(400, "Google did not return a refresh token; remove the app at myaccount.google.com/permissions and connect again.");
  const channel = await myChannel(tok.access_token);
  if (!channel) {
    // The API cannot create channels: the user must create one on YouTube first.
    return NextResponse.redirect(new URL(`/settings?youtube=no_channel`, url));
  }
  // Tokens are stored encrypted, server-side only (service role; RLS hides this table from the browser).
  const { error } = await supabaseAdmin().from("youtube_connections").upsert({
    user_id: user.id, channel_id: channel.id, channel_title: channel.title, scopes: tok.scope.split(" "),
    access_token_enc: encrypt(tok.access_token), refresh_token_enc: encrypt(tok.refresh_token),
    expires_at: new Date(Date.now() + tok.expires_in * 1000).toISOString(),
    audited_project: process.env.YOUTUBE_API_AUDITED === "true",
  });
  if (error) throw error;
  const res = NextResponse.redirect(new URL(`/settings?youtube=connected`, url));
  res.cookies.delete("yt_oauth_state");
  return res;
});
