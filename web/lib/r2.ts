import { createHash, createHmac } from "node:crypto";

/** Presigned GET URL for a private Cloudflare R2 object (AWS Signature V4, query-string form). No SDK needed. */
export function r2PresignedGet(key: string, seconds: number, download?: string, now = new Date()): string {
  return r2Presigned("GET", key, seconds, download, now);
}

/** Presigned PUT URL: the browser uploads straight to R2 (needs the bucket CORS policy). Only "host" is signed,
 *  so the browser may send its own Content-Type. */
export function r2PresignedPut(key: string, seconds = 3600, now = new Date()): string {
  return r2Presigned("PUT", key, seconds, undefined, now);
}

function r2Presigned(method: "GET" | "PUT", key: string, seconds: number, download?: string, now = new Date()): string {
  const account = process.env.R2_ACCOUNT_ID, ak = process.env.R2_ACCESS_KEY_ID, sk = process.env.R2_SECRET_ACCESS_KEY;
  if (!account || !ak || !sk) throw new Error("R2 is not configured: set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY");
  const bucket = process.env.R2_BUCKET ?? "ycb-media";
  const endpoint = new URL(process.env.R2_ENDPOINT ?? `https://${account}.r2.cloudflarestorage.com`);
  const enc = (s: string) => encodeURIComponent(s).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
  const path = "/" + [bucket, ...key.split("/")].map(enc).join("/");
  const amzDate = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const day = amzDate.slice(0, 8);
  const scope = `${day}/auto/s3/aws4_request`;
  const q: Record<string, string> = {
    "X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": `${ak}/${scope}`, "X-Amz-Date": amzDate,
    "X-Amz-Expires": String(Math.min(Math.max(1, Math.floor(seconds)), 604800)), "X-Amz-SignedHeaders": "host",
  };
  if (download) q["response-content-disposition"] = `attachment; filename="${download.replace(/[^\w.-]/g, "_")}"`;
  const query = Object.keys(q).sort().map((k) => `${enc(k)}=${enc(q[k])}`).join("&");
  const canonical = [method, path, query, `host:${endpoint.host}`, "", "host", "UNSIGNED-PAYLOAD"].join("\n");
  const toSign = ["AWS4-HMAC-SHA256", amzDate, scope, createHash("sha256").update(canonical).digest("hex")].join("\n");
  const h = (k: Buffer | string, d: string) => createHmac("sha256", k).update(d).digest();
  const signingKey = h(h(h(h("AWS4" + sk, day), "auto"), "s3"), "aws4_request");
  const sig = createHmac("sha256", signingKey).update(toSign).digest("hex");
  return `${endpoint.origin}${path}?${query}&X-Amz-Signature=${sig}`;
}
