import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { YT_SCOPES, missingYoutubeEnv } from "@/lib/youtube";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CapForm, DeleteData, YoutubeConnection } from "@/components/settings-forms";
import { ThemeToggle } from "@/components/theme-toggle";

export const metadata = { title: "Settings" };

const NOTES: Record<string, string> = {
  connected: "YouTube connected.",
  denied: "You declined the YouTube permission screen. Nothing was connected.",
  no_channel: "That Google account has no YouTube channel. Create one on YouTube first (the API cannot), then connect again.",
};

export default async function Settings({ searchParams }: { searchParams: Promise<{ youtube?: string }> }) {
  const { youtube } = await searchParams;
  const { sb } = await requireUser();
  const [{ data: profile }, { data: conn }] = await Promise.all([
    sb.from("users").select("*").single(),
    sb.from("youtube_connection_status").select("*").maybeSingle(),
  ]);
  const missing = missingYoutubeEnv();
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-serif text-2xl">Settings</h1>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">Appearance <ThemeToggle /></div>
      </div>
      {youtube && NOTES[youtube] && <p className="rounded-md border bg-card p-3 text-sm">{NOTES[youtube]}</p>}

      <Card><CardHeader><CardTitle>Spending</CardTitle><CardDescription>Generation pauses before any step that would cross the cap. Raising it resumes paused jobs.</CardDescription></CardHeader>
        <CardContent><CapForm cap={Number(profile?.monthly_cap_usd ?? 10)} tier={profile?.default_tier ?? "standard"} /></CardContent></Card>

      <Card>
        <CardHeader><CardTitle>YouTube</CardTitle>
          <CardDescription>We never ask for your Google password. Tokens are encrypted and kept on the server only.</CardDescription></CardHeader>
        <CardContent className="space-y-3 text-sm">
          {conn ? (
            <div className="space-y-1"><div>Connected to <b>{conn.channel_title}</b> ({conn.channel_id})</div>
              <Badge variant={conn.audited_project ? "success" : "warning"}>{conn.audited_project ? "Audited API project: public uploads allowed" : "Unaudited API project: uploads are private-only"}</Badge></div>
          ) : <p>Not connected.</p>}
          <div>
            <div className="font-medium">Permissions requested, in plain language</div>
            <ul className="mt-1 list-disc pl-5 text-muted-foreground">{YT_SCOPES.map((s) => <li key={s.scope}><code className="text-xs">{s.scope.split("/").pop()}</code>: {s.why}</li>)}</ul>
          </div>
          {missing.length ? <p className="text-destructive">Server not configured for YouTube: {missing.join(", ")}. See docs/GOOGLE-CLOUD-SETUP.md.</p> : <YoutubeConnection connected={!!conn} />}
          <p className="text-xs text-muted-foreground">Until your Google Cloud project passes YouTube&apos;s API compliance audit, YouTube locks API uploads to private. Use the MP4 download for manual upload in the meantime. The audit checklist is in docs/YOUTUBE-AUDIT.md.</p>
        </CardContent>
      </Card>

      <Card><CardHeader><CardTitle>Your data</CardTitle>
        <CardDescription>Delete connected Google data and media, or your whole account. See <Link className="underline" href="/legal/data-deletion">data deletion</Link>.</CardDescription></CardHeader>
        <CardContent><DeleteData /></CardContent></Card>
    </div>
  );
}
