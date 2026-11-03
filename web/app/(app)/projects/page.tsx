import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { Badge } from "@/components/ui/badge";

export const metadata = { title: "Projects" };

export default async function Projects() {
  const { sb } = await requireUser();
  const { data } = await sb.from("projects").select("id,title,status,tier,target_minutes,created_at").order("created_at", { ascending: false });
  return (
    <div className="mx-auto max-w-5xl space-y-3">
      <h1 className="font-serif text-2xl">Projects</h1>
      {(data ?? []).map((p) => (
        <Link key={p.id} href={`/projects/${p.id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-card p-3 hover:bg-muted">
          <span className="min-w-0 font-medium">{p.title}</span>
          <span className="flex gap-1"><Badge variant="outline">{p.target_minutes} min</Badge><Badge variant="outline">{p.tier}</Badge><Badge variant="secondary">{p.status}</Badge></span>
        </Link>
      ))}
      {!data?.length && <p className="text-sm text-muted-foreground">Pick an idea on an Idea Board to start a project.</p>}
    </div>
  );
}
