import "server-only";

// Least privilege: upload + read-only. Nothing that can delete videos or manage the channel.
export const YT_SCOPES = [
  { scope: "https://www.googleapis.com/auth/youtube.upload", why: "Upload the videos you approve. It cannot delete or edit your other videos." },
  { scope: "https://www.googleapis.com/auth/youtube.readonly", why: "Read your channel name and the status of uploaded videos, to show them here." },
];

export function oauthUrl(state: string) {
  const p = new URLSearchParams({
    client_id: process.env.GOOGLE_OAUTH_CLIENT_ID!,
    redirect_uri: `${process.env.NEXT_PUBLIC_APP_URL}/api/youtube/callback`,
    response_type: "code",
    scope: ["openid", ...YT_SCOPES.map((s) => s.scope)].join(" "),
    access_type: "offline",
    include_granted_scopes: "true",
    prompt: "consent",
    state,
  });
  return `https://accounts.google.com/o/oauth2/v2/auth?${p}`;
}

export async function exchangeCode(code: string) {
  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code, client_id: process.env.GOOGLE_OAUTH_CLIENT_ID!, client_secret: process.env.GOOGLE_OAUTH_CLIENT_SECRET!,
      redirect_uri: `${process.env.NEXT_PUBLIC_APP_URL}/api/youtube/callback`, grant_type: "authorization_code",
    }),
  });
  if (!r.ok) throw new Error(`token exchange failed: ${r.status}`);
  return (await r.json()) as { access_token: string; refresh_token?: string; expires_in: number; scope: string; id_token?: string };
}

export async function myChannel(accessToken: string) {
  const r = await fetch("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!r.ok) return null;
  const j = await r.json();
  const c = j.items?.[0];
  return c ? { id: c.id as string, title: c.snippet.title as string } : null;
}

export function missingYoutubeEnv() {
  return ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "TOKEN_ENCRYPTION_KEY", "NEXT_PUBLIC_APP_URL"].filter((k) => !process.env[k]);
}
