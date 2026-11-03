import "server-only";
import { supabaseAdmin } from "./supabase/server";
import { estimate, type Action, type Tier } from "./pricing";

export class CapError extends Error {}

/**
 * Enqueue a worker job after the user accepted the cost modal.
 * Re-checks the hard cap server-side (the modal is not trusted) and stores the accepted estimate.
 */
export async function enqueueJob(opts: {
  userId: string;
  kind: string;
  payload: Record<string, unknown>;
  action: Action;
  tier: Tier;
  minutes?: number;
  projectId?: string | null;
  renderId?: string | null;
  stage?: string | null;
  idempotencyKey?: string | null;
}) {
  const admin = supabaseAdmin();
  const est = estimate(opts.action, opts.tier, opts.minutes ?? 12);
  const { data: ok, error: capErr } = await admin.rpc("within_cap", { p_user: opts.userId, p_amount: est.total_usd });
  if (capErr) throw capErr;
  if (!ok) throw new CapError(`This would exceed your monthly hard cap. Estimated ${est.total_usd.toFixed(2)} USD. Raise the cap in Settings.`);
  await admin.from("estimates").insert({
    user_id: opts.userId, project_id: opts.projectId ?? null, action: opts.action, tier: opts.tier,
    lines: est.lines, total_usd: est.total_usd, accepted: true,
  });
  const { data, error } = await admin
    .from("jobs")
    .upsert(
      {
        user_id: opts.userId, kind: opts.kind, payload: opts.payload, project_id: opts.projectId ?? null,
        render_id: opts.renderId ?? null, stage: opts.stage ?? null, idempotency_key: opts.idempotencyKey ?? null,
      },
      { onConflict: "idempotency_key", ignoreDuplicates: !!opts.idempotencyKey },
    )
    .select()
    .maybeSingle();
  if (error) throw error;
  return { job: data, estimate: est };
}

/** Start a whole-video render: new render version + first stage job (the worker chains the rest). */
export async function startRender(userId: string, projectId: string, tier: Tier, minutes: number) {
  const admin = supabaseAdmin();
  const { data: last } = await admin.from("renders").select("version").eq("project_id", projectId).order("version", { ascending: false }).limit(1).maybeSingle();
  const version = (last?.version ?? 0) + 1;
  const est = estimate("whole_video", tier, minutes);
  const { data: render, error } = await admin.from("renders").insert({ project_id: projectId, version, estimate_usd: est.total_usd }).select().single();
  if (error) throw error;
  await admin.from("projects").update({ status: "rendering" }).eq("id", projectId);
  const { job } = await enqueueJob({
    userId, kind: "render_stage", stage: "script", payload: {}, action: "whole_video", tier, minutes,
    projectId, renderId: render.id, idempotencyKey: `render:${render.id}:script`,
  });
  return { render, job };
}
