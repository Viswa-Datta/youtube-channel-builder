"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { supabaseBrowser } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function Login() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [google, setGoogle] = useState(false);
  const sb = supabaseBrowser();
  useEffect(() => {
    // Ask Supabase which sign-in methods are switched on, so the page never offers one that would fail.
    fetch(`${process.env.NEXT_PUBLIC_SUPABASE_URL}/auth/v1/settings`, { headers: { apikey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY! } })
      .then((r) => (r.ok ? r.json() : null)).then((j) => setGoogle(!!j?.external?.google)).catch(() => setGoogle(false));
  }, []);
  const redirectTo = typeof window !== "undefined" ? `${window.location.origin}/auth/callback` : undefined;

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>YouTube Channel Builder</CardTitle>
          <CardDescription>Sign in with a link sent to your email. Your projects are saved to your account.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {sent ? (
            <p className="text-sm">Check {email} for a sign-in link, and open it in this browser.</p>
          ) : (
            <form className="space-y-2" onSubmit={async (e) => {
              e.preventDefault();
              setErr(null);
              const { error } = await sb.auth.signInWithOtp({ email, options: { emailRedirectTo: redirectTo } });
              if (error) setErr(error.message);
              else setSent(true);
            }}>
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
              <Button className="w-full" type="submit">Email me a sign-in link</Button>
              {err && <p className="text-sm text-destructive">{err}</p>}
            </form>
          )}
          {google && (
            <>
              <div className="text-center text-xs text-muted-foreground">or</div>
              <Button className="w-full" variant="outline"
                onClick={() => sb.auth.signInWithOAuth({ provider: "google", options: { redirectTo } })}>
                Continue with Google
              </Button>
            </>
          )}
          <p className="text-xs text-muted-foreground">
            Signing in with Google here does not connect YouTube. YouTube is connected separately in Settings, with its own permission screen.
            See <Link className="underline" href="/legal/privacy">Privacy</Link> and <Link className="underline" href="/legal/terms">Terms</Link>.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
