import { NextResponse } from "next/server";
import { createReadStream, statSync } from "node:fs";
import { join, normalize } from "node:path";
import { Readable } from "node:stream";
import { apiUser, handler, HttpError, owned } from "@/lib/api";

// Development-only file server for STORAGE_DRIVER=local (production uses Supabase signed URLs).
// Supports HTTP range requests (video seeking) and ?vtt=1 to serve captions.srt as WebVTT for <track>.
export const GET = handler(async (req: Request, ctx: { params: Promise<{ key: string[] }> }) => {
  if (process.env.STORAGE_DRIVER !== "local") throw new HttpError(404, "not found");
  const { key } = await ctx.params;
  const { sb } = await apiUser();
  if (key[0] !== "projects" || !key[1]) throw new HttpError(404, "not found");
  await owned(sb, "projects", key[1]); // ownership via RLS
  const root = process.env.LOCAL_STORAGE_DIR ?? join(process.cwd(), "..", ".storage");
  const path = normalize(join(root, ...key));
  if (!path.startsWith(normalize(root))) throw new HttpError(400, "bad path");
  const url = new URL(req.url);
  const type = path.endsWith(".mp4") ? "video/mp4" : path.endsWith(".jpg") ? "image/jpeg" : path.endsWith(".srt") ? "application/x-subrip"
    : path.endsWith(".mp3") ? "audio/mpeg" : path.endsWith(".wav") ? "audio/wav" : path.endsWith(".json") ? "application/json" : "text/plain";
  if (url.searchParams.get("vtt") && path.endsWith(".srt")) {
    const { readFileSync } = await import("node:fs");
    const vtt = "WEBVTT\n\n" + readFileSync(path, "utf8").replace(/(\d\d:\d\d:\d\d),(\d\d\d)/g, "$1.$2");
    return new NextResponse(vtt, { headers: { "Content-Type": "text/vtt" } });
  }
  const size = statSync(path).size;
  const headers: Record<string, string> = { "Content-Type": type, "Accept-Ranges": "bytes" };
  const dl = url.searchParams.get("download");
  if (dl) headers["Content-Disposition"] = `attachment; filename="${dl.replace(/[^\w.-]/g, "_")}"`;
  const range = req.headers.get("range")?.match(/bytes=(\d*)-(\d*)/);
  if (range) {
    const start = Number(range[1] || 0);
    const end = range[2] ? Number(range[2]) : Math.min(size - 1, start + 8 * 1024 * 1024);
    return new NextResponse(Readable.toWeb(createReadStream(path, { start, end })) as ReadableStream, {
      status: 206, headers: { ...headers, "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": String(end - start + 1) },
    });
  }
  return new NextResponse(Readable.toWeb(createReadStream(path)) as ReadableStream, { headers: { ...headers, "Content-Length": String(size) } });
});
