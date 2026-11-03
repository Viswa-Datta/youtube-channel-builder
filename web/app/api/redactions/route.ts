import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned } from "@/lib/api";

// Manual blur boxes, graphic cut-aways, audio mutes and caption omissions from the Review Studio.
const Req = z.object({
  project_id: z.string().uuid(),
  asset_id: z.string().uuid().nullable().optional(),
  scene_idx: z.number().int().min(0).nullable().optional(),
  kind: z.enum(["manual_box", "graphic_cutaway", "audio_mute", "audio_bleep", "caption_omit", "text_pii"]),
  t_start: z.number().min(0),
  t_end: z.number().min(0),
  box: z.object({ x: z.number().min(0).max(1), y: z.number().min(0).max(1), w: z.number().min(0).max(1), h: z.number().min(0).max(1) }).nullable().optional(),
  reason: z.string().max(300).optional(),
});

export const POST = handler(async (req: Request) => {
  const { sb } = await apiUser();
  const b = await body(req, Req);
  await owned(sb, "projects", b.project_id);
  const { data, error } = await sb.from("redaction_plan").insert({ ...b, origin: "manual" }).select().single();
  if (error) throw error;
  await sb.from("projects").update({ approved_at: null }).eq("id", b.project_id);
  return NextResponse.json({ redaction: data, rerender_needed: true });
});
