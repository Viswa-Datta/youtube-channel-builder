import { NextResponse } from "next/server";

// Liveness + which integrations are configured (names only, never values).
export function GET() {
  const keys = ["NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET", "TOKEN_ENCRYPTION_KEY", "NEXT_PUBLIC_APP_URL", "SENTRY_DSN"];
  return NextResponse.json({ ok: true, configured: Object.fromEntries(keys.map((k) => [k, Boolean(process.env[k])])) });
}
