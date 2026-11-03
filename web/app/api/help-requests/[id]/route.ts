import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";

const THIRD_PARTY = ["youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "reddit.com", "rumble.com", "vimeo.com"];
const LICENSES = ["Public Domain", "Public Record with release terms", "Licensed", "Commentary-only"] as const;

const Req = z.discriminatedUnion("action", [
  z.object({ action: z.literal("skip") }),
  z.object({ action: z.literal("reopen") }),
  z.object({
    action: z.literal("complete"),
    key: z.string().min(10),
    kind: z.enum(["clip", "image"]),
    scene_id: z.string().uuid(),
    title: z.string().min(2).max(300),
    agency: z.string().min(2).max(200),
    release_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable(),
    source_url: z.string().url(),
    license: z.enum(LICENSES),
    rights_notes: z.string().min(10).max(2000),
    start_sec: z.number().min(0).nullable(),
    end_sec: z.number().min(0).nullable(),
    official_channel: z.boolean(), // user confirms a YouTube/Vimeo link is the agency's own upload
    confirm_not_sealed_or_leaked: z.literal(true),
  }),
]);

/** Help requests: things only the user can do (save a clip the app may not download, find a source). */
export const POST = handler(async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  const { id } = await ctx.params;
  const { sb, user } = await apiUser();
  const h = await owned<{ id: string; project_id: string; kind: string }>(sb, "help_requests", id);
  const b = await body(req, Req);
  if (b.action === "skip" || b.action === "reopen") {
    const { error } = await sb.from("help_requests").update({ status: b.action === "skip" ? "skipped" : "open", resolved_at: b.action === "skip" ? new Date().toISOString() : null }).eq("id", id);
    if (error) throw new HttpError(409, error.message);
    return NextResponse.json({ ok: true });
  }
  if (!b.key.startsWith(`projects/${h.project_id}/uploads/`)) throw new HttpError(400, "That upload does not belong to this project.");
  if (b.end_sec !== null && b.start_sec !== null && b.end_sec <= b.start_sec) throw new HttpError(422, "End time must be after start time.");
  const host = b.source_url.toLowerCase();
  if (THIRD_PARTY.some((t) => host.includes(t)) && !b.official_channel)
    throw new HttpError(422, "That link is a video-sharing site. Use it only if it is the agency's own official upload (tick the box), otherwise link the agency or court page.");
  const { data: scene } = await sb.from("scenes").select("id, project_id").eq("id", b.scene_id).maybeSingle();
  if (!scene || scene.project_id !== h.project_id) throw new HttpError(400, "Pick a scene from this project.");
  const { data: asset, error } = await sb.from("assets").insert({
    project_id: h.project_id, scene_id: b.scene_id, kind: b.kind, tier: "real", storage_path: b.key, title: b.title,
    source_name: THIRD_PARTY.some((t) => host.includes(t)) ? "Agency upload (official channel)" : "Agency / court release",
    agency: b.agency, release_date: b.release_date, source_url: b.source_url, license: b.license, rights_notes: b.rights_notes,
    rights_checked: true, rights_checked_by: user.id, rights_checked_at: new Date().toISOString(),
    verified_real: !!b.release_date, selected: true, in_sec: b.start_sec, out_sec: b.end_sec,
    meta: { uploaded_by_user: true, help_request_id: id, official_channel: b.official_channel },
  }).select("id").single();
  if (error) throw new HttpError(409, error.message);
  await sb.from("help_requests").update({ status: "done", asset_id: asset.id, resolved_at: new Date().toISOString() }).eq("id", id);
  await sb.from("projects").update({ approved_at: null }).eq("id", h.project_id);
  return NextResponse.json({ ok: true, asset_id: asset.id });
});
