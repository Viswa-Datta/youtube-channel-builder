import { NextResponse } from "next/server";
import { randomBytes } from "node:crypto";
import { apiUser, handler, HttpError } from "@/lib/api";
import { missingYoutubeEnv, oauthUrl } from "@/lib/youtube";

// Starts Google OAuth for YouTube. We never see the user's Google password.
export const GET = handler(async () => {
  await apiUser();
  const missing = missingYoutubeEnv();
  if (missing.length) throw new HttpError(503, `YouTube connection is not configured on this server: set ${missing.join(", ")}`, { missing });
  const state = randomBytes(24).toString("hex");
  const res = NextResponse.redirect(oauthUrl(state));
  res.cookies.set("yt_oauth_state", state, { httpOnly: true, secure: true, sameSite: "lax", maxAge: 600, path: "/api/youtube" });
  return res;
});
