import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { IntakeForm } from "@/components/intake-form";
import { costContext } from "@/lib/context";

export const metadata = { title: "New channel" };

export default async function NewChannel() {
  const { ctx, defaultTier } = await costContext();
  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>Niche intake</CardTitle>
          <CardDescription>
            YouTube&apos;s API cannot create channels. Create the channel on YouTube yourself; this kit gives you the name, handle,
            description and artwork prompts to paste in, then you connect it in Settings.
          </CardDescription>
        </CardHeader>
        <CardContent><IntakeForm ctx={ctx} defaultTier={defaultTier} /></CardContent>
      </Card>
    </div>
  );
}
