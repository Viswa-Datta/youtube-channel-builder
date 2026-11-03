import "server-only";
import { supabaseAdmin } from "./supabase/server";

/** Short-lived signed URL for a private project object. Callers must check ownership first. */
export async function signedUrl(key: string | null | undefined, seconds = 3600, download?: string) {
  if (!key) return null;
  if (process.env.STORAGE_DRIVER === "local") return `/api/files/${key}${download ? `?download=${encodeURIComponent(download)}` : ""}`;
  const { data, error } = await supabaseAdmin()
    .storage.from(process.env.STORAGE_BUCKET ?? "projects")
    .createSignedUrl(key, seconds, download ? { download } : undefined);
  if (error) return null;
  return data.signedUrl;
}
