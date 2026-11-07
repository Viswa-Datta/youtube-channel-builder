import { createClient } from "@supabase/supabase-js";
import { notFound } from "next/navigation";

// Public page linked from each YouTube description: every source, clip and photo used in the video, with its
// author and licence (Creative Commons attribution in full). Read-only, no sign-in.
export const revalidate = 3600;

export async function generateMetadata() {
  return { title: "Video credits", robots: { index: false } };
}

export default async function CreditsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const sb = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, { auth: { persistSession: false } });
  const { data } = await sb.rpc("public_credits", { p_render: id });
  const row = (data as { title: string; credits: string }[] | null)?.[0];
  if (!row) notFound();
  const linkify = (line: string) =>
    line.split(/(https?:\/\/[^\s)]+)/g).map((part, i) =>
      /^https?:\/\//.test(part) ? <a key={i} href={part} className="break-all underline" rel="nofollow noreferrer" target="_blank">{part}</a> : part);
  return (
    <main className="mx-auto max-w-3xl space-y-4 p-6">
      <h1 className="font-serif text-2xl">Credits: {row.title}</h1>
      <p className="text-sm text-muted-foreground">Every source, clip and photo used in this video, with its author and licence.</p>
      <ul className="space-y-1.5 text-sm">
        {row.credits.split("\n").filter((l) => l.trim() && !l.endsWith(":")).map((l, i) => <li key={i}>{linkify(l.replace(/^- /, ""))}</li>)}
      </ul>
    </main>
  );
}
