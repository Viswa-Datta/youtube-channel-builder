import Link from "next/link";
import { requireUser } from "@/lib/supabase/server";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata = { title: "Channels" };

export default async function Channels() {
  const { sb } = await requireUser();
  const { data } = await sb.from("channels").select("*").order("created_at");
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex items-center justify-between"><h1 className="font-serif text-2xl">Channels</h1><Button asChild><Link href="/channels/new">New channel</Link></Button></div>
      <div className="grid gap-3 sm:grid-cols-2">
        {(data ?? []).map((c) => (
          <Card key={c.id}>
            <CardHeader>
              <CardTitle>{c.name ?? c.niche}</CardTitle>
              <CardDescription>{c.niche} · {c.target_minutes} min · {c.faceless ? "faceless" : "on camera"}</CardDescription>
              <div className="flex gap-2 pt-2">
                <Button size="sm" variant="outline" asChild><Link href={`/channels/${c.id}/kit`}>Kit</Link></Button>
                <Button size="sm" variant="outline" asChild><Link href={`/channels/${c.id}/ideas`}>Ideas</Link></Button>
              </div>
            </CardHeader>
          </Card>
        ))}
      </div>
    </div>
  );
}
