import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";

const THIRD_PARTY = ["youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "reddit.com", "rumble.com"];

const Req = z.discriminatedUnion("action", [
  z.object({ action: z.literal("select") }),
  z.object({ action: z.literal("unselect") }),
  z.object({
    action: z.literal("rights_check"),
    license: z.enum(["Public Domain", "Public Record with release terms", "Licensed", "Commentary-only", "Do Not Use"]),
    agency: z.string().min(2).max(200),
    release_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    source_url: z.string().url(),
    rights_notes: z.string().min(10).max(2000),
    verified_real: z.boolean(),
    confirm_not_sealed_or_leaked: z.literal(true),
  }),
  z.object({ action: z.literal("graphic"), graphic: z.boolean() }),
]);

export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  const a = await owned<{ id: string; tier: string; project_id: string; scene_id: string; kind: string }>(sb, "assets", id);
  const b = await body(req, Req);
  let patch: Record<string, unknown> = {};
  if (b.action === "select") {
    if (a.kind === "thumbnail") await sb.from("assets").update({ selected: false }).eq("project_id", a.project_id).eq("kind", "thumbnail");
    patch = { selected: true };
  } else if (b.action === "unselect") patch = { selected: false };
  else if (b.action === "graphic") patch = { graphic: b.graphic };
  else {
    if (b.verified_real && a.tier !== "real") throw new HttpError(422, "Only case footage can be marked verified real. Stock is always B-roll.");
    if (THIRD_PARTY.some((h) => b.source_url.toLowerCase().includes(h)))
      throw new HttpError(422, "That is a third-party video host. Link the original agency or court release, not a re-upload.");
    patch = { license: b.license, agency: b.agency, release_date: b.release_date, source_url: b.source_url, rights_notes: b.rights_notes,
      rights_checked: b.license !== "Do Not Use", rights_checked_by: user.id, rights_checked_at: new Date().toISOString(),
      verified_real: b.verified_real };
  }
  // CHECK constraints in the database are the final word (stock never real; verified real needs full provenance).
  const { error } = await sb.from("assets").update(patch).eq("id", id);
  if (error) throw new HttpError(409, error.message);
  await sb.from("projects").update({ approved_at: null }).eq("id", a.project_id);
  return NextResponse.json({ ok: true });
});
