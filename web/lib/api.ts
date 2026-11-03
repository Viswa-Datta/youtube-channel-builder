import "server-only";
import { NextResponse } from "next/server";
import { ZodError, type ZodSchema } from "zod";
import { supabaseServer } from "./supabase/server";
import { CapError } from "./jobs";

export async function apiUser() {
  const sb = await supabaseServer();
  const { data } = await sb.auth.getUser();
  if (!data.user) throw new HttpError(401, "Sign in first");
  return { sb, user: data.user };
}

export class HttpError extends Error {
  constructor(public status: number, message: string, public extra?: Record<string, unknown>) {
    super(message);
  }
}

export async function body<T>(req: Request, schema: ZodSchema<T>): Promise<T> {
  return schema.parse(await req.json());
}

/** Wrap a handler: consistent JSON errors (401/403/404/409/422) and no stack traces to the client. */
export function handler<A extends unknown[]>(fn: (...a: A) => Promise<Response>) {
  return async (...a: A) => {
    try {
      return await fn(...a);
    } catch (e) {
      if (e instanceof HttpError) return NextResponse.json({ error: e.message, ...e.extra }, { status: e.status });
      if (e instanceof CapError) return NextResponse.json({ error: e.message, code: "cap" }, { status: 402 });
      if (e instanceof ZodError) return NextResponse.json({ error: "Invalid request", issues: e.issues }, { status: 422 });
      const msg = (e as { message?: string })?.message ?? "error";
      if (msg.includes("publish blocked") || msg.includes("approve blocked") || msg.includes("violates check constraint"))
        return NextResponse.json({ error: msg }, { status: 409 });
      console.error(e);
      return NextResponse.json({ error: "Something went wrong" }, { status: 500 });
    }
  };
}

/** RLS-scoped lookup: returns the row only if the signed-in user owns it. */
export async function owned<T = Record<string, unknown>>(sb: Awaited<ReturnType<typeof supabaseServer>>, table: string, id: string) {
  const { data } = await sb.from(table).select("*").eq("id", id).maybeSingle();
  if (!data) throw new HttpError(404, `${table.slice(0, -1)} not found`);
  return data as T;
}
