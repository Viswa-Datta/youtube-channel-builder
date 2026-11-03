import { NextResponse } from "next/server";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { apiUser, body, handler, owned, HttpError } from "@/lib/api";
import { r2PresignedPut } from "@/lib/r2";

// Footage Desk: a short-lived link the browser uses to upload a clip or photo straight to R2.
const Req = z.object({
  project_id: z.string().uuid(),
  filename: z.string().min(1).max(300),
  content_type: z.string().regex(/^(video|image)\/[\w.+-]+$/),
  size: z.number().int().positive().max(4_900_000_000), // single-PUT limit on R2 is ~5 GB
});

export const POST = handler(async (req: Request) => {
  if (process.env.STORAGE_DRIVER !== "r2") throw new HttpError(501, "Uploads need Cloudflare R2 storage (STORAGE_DRIVER=r2).");
  const { sb } = await apiUser();
  const b = await body(req, Req);
  await owned(sb, "projects", b.project_id);
  const ext = (b.filename.match(/\.([a-z0-9]{2,5})$/i)?.[1] ?? (b.content_type.startsWith("image/") ? "jpg" : "mp4")).toLowerCase();
  const key = `projects/${b.project_id}/uploads/${randomUUID()}.${ext}`;
  return NextResponse.json({ key, url: r2PresignedPut(key, 3600) });
});
