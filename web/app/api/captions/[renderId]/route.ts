import { NextResponse } from "next/server";
import { apiUser, handler, HttpError } from "@/lib/api";
import { signedUrl } from "@/lib/storage";

// Same-origin WebVTT for the Studio <track>: browsers refuse cross-origin caption files unless the bucket sends CORS
// headers, so the server fetches the private object and relays it. Ownership is enforced by RLS on renders.
export const GET = handler(async (req: Request, ctx: { params: Promise<{ renderId: string }> }) => {
  const { renderId } = await ctx.params;
  const { sb } = await apiUser();
  const { data: render } = await sb.from("renders").select("srt_path").eq("id", renderId).maybeSingle();
  if (!render?.srt_path) throw new HttpError(404, "not found");
  const url = await signedUrl(render.srt_path.replace(/\.srt$/, ".vtt"), 120);
  if (!url) throw new HttpError(404, "not found");
  const r = await fetch(url.startsWith("/") ? new URL(url, req.url) : url, { cache: "no-store", headers: { cookie: req.headers.get("cookie") ?? "" } });
  if (!r.ok) throw new HttpError(404, "captions not found");
  return new NextResponse(await r.text(), { headers: { "Content-Type": "text/vtt; charset=utf-8", "Cache-Control": "private, max-age=300" } });
});
