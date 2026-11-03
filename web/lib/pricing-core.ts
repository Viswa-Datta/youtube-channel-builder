// Pure cost estimator. Mirrors worker/ycb/pricing.py line for line; tests/pricing.test.ts compares both.
export type Tier = "free" | "standard" | "premium";
export type Action = "channel_kit" | "channel_kit_item" | "ideas" | "production_pack" | "whole_video" | "full";

export interface Line {
  stage: string;
  provider: string;
  plan: string;
  unit_type: string;
  units: number;
  cost_usd: number;
  paid: boolean;
  free_alternative: string;
}

export interface Estimate {
  action: Action;
  tier: Tier;
  minutes: number;
  scenes: number;
  words: number;
  voice_characters: number;
  lines: Line[];
  total_usd: number;
  per_10_min_usd: number;
  guardrail_violation: string | null;
  prices_last_verified: string;
  math: string;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Pricing = any;

// Rounds the exact binary value of x, like Python's round() (toFixed does not pre-scale, so no drift).
const round = (x: number, d: number) => Number(x.toFixed(d));

export function wordsFor(P: Pricing, minutes: number) {
  return Math.round(minutes * P.words_per_minute);
}
export function voiceChars(P: Pricing, minutes: number) {
  return Math.round(wordsFor(P, minutes) * P.chars_per_word);
}

function llmCost(P: Pricing, key: string, tin: number, tout: number) {
  const m = P.llm[key];
  return (tin / 1e6) * m.in_per_m + (tout / 1e6) * m.out_per_m;
}

function llmLine(P: Pricing, stage: string, key: string, tin: number, tout: number): Line {
  const m = P.llm[key];
  return {
    stage,
    provider: key.split(":")[0],
    plan: m.label,
    unit_type: "tokens",
    units: tin + tout,
    cost_usd: round(llmCost(P, key, tin, tout), 6),
    paid: m.paid,
    free_alternative: m.paid ? `${P.llm[P.tiers.free.llm].label} — $0` : "",
  };
}

export function estimateWith(
  P: Pricing,
  action: Action,
  tier: Tier,
  minutes = 12,
  scenes = 7,
  overrides: { llm?: string; tts?: string; compute?: string } = {},
): Estimate {
  const t = P.tiers[tier];
  const llm = overrides.llm ?? t.llm;
  const tts = overrides.tts ?? t.tts;
  const compute = overrides.compute ?? t.compute;
  const polish: string | null = t.polish_llm;
  const b = P.agent_token_budgets;
  const tpw = P.tokens_per_word;
  const words = wordsFor(P, minutes);
  const lines: Line[] = [];

  if (action === "channel_kit") lines.push(llmLine(P, "Channel Agent", llm, b.channel_kit.in, b.channel_kit.out));
  if (action === "channel_kit_item") lines.push(llmLine(P, "Channel Agent (one item)", llm, b.channel_kit_item.in, b.channel_kit_item.out));
  if (action === "ideas") lines.push(llmLine(P, "Ideas Agent (25 ideas)", llm, b.ideas.in, b.ideas.out));
  if (action === "production_pack" || action === "full") {
    lines.push(llmLine(P, "Research Agent", llm, b.research.in, b.research.out));
    lines.push(llmLine(P, "Script Agent (draft)", llm, b.script_draft.in, b.script_draft.out));
    lines.push(llmLine(P, "Visual Director Agent", llm, b.visual_director.in, b.visual_director.out));
    lines.push({ stage: "Footage search (archives, Pexels, Pixabay)", provider: "footage", plan: "Free APIs", unit_type: "requests", units: scenes * 4, cost_usd: 0, paid: false, free_alternative: "" });
  }
  if (action === "whole_video" || action === "full") {
    const tin = scenes * b.script_expand_overhead_in_per_scene;
    const tout = Math.trunc(words * tpw);
    lines.push(llmLine(P, "Script Agent (expand to full narration)", llm, tin, tout));
    if (polish) lines.push(llmLine(P, "Script polish pass", polish, Math.trunc(words * tpw) + 1500, Math.trunc(words * tpw)));
    lines.push(llmLine(P, "Editor + SEO Agent", llm, Math.trunc(words * tpw) + b.editor_seo_overhead.in, b.editor_seo_overhead.out));
    const chars = voiceChars(P, minutes);
    const v = P.tts[tts];
    lines.push({
      stage: "Voice Agent (narration)", provider: tts.split(":")[0], plan: v.label, unit_type: "characters", units: chars,
      cost_usd: round((chars / 1e6) * v.per_m_chars, 6), paid: v.paid,
      free_alternative: v.paid ? `${P.tts[P.tiers.free.tts].label} — $0` : "",
    });
    const c = P.compute[compute];
    const hours = minutes * P.render_hours_per_video_minute;
    lines.push({
      stage: "Render worker (FFmpeg, blur, mix)", provider: "compute", plan: c.label, unit_type: "seconds", units: Math.trunc(hours * 3600),
      cost_usd: round(hours * c.per_render_hour, 6), paid: c.paid,
      free_alternative: c.paid ? `${P.compute.self_hosted.label} — $0` : "",
    });
    const s = P.storage.supabase_or_r2;
    const gb = minutes * s.gb_per_minute_1080p * 1.5;
    const free = tier === "free";
    lines.push({
      stage: "Storage (1 month)", provider: "storage", plan: free ? "Local disk / Supabase free 1 GB" : s.label, unit_type: "gb",
      units: Math.ceil(gb * 1000), cost_usd: free ? 0 : round(gb * s.per_gb_month, 6), paid: !free,
      free_alternative: free ? "" : "Local disk / Supabase free 1 GB — $0",
    });
  }
  const total = round(lines.reduce((a, l) => a + l.cost_usd, 0), 4);
  const per10 = minutes ? round((total / minutes) * 10, 4) : total;
  let guard: string | null = null;
  if (tier === "standard" && (action === "whole_video" || action === "full") && per10 > P.guardrail.standard_max_usd_per_10_min) {
    guard = `Standard must stay under $${P.guardrail.standard_max_usd_per_10_min.toFixed(2)} per 10 min; move this feature to Premium.`;
  }
  return {
    action, tier, minutes, scenes, words, voice_characters: voiceChars(P, minutes), lines, total_usd: total,
    per_10_min_usd: per10, guardrail_violation: guard, prices_last_verified: P.last_verified,
    math: `${minutes} min x ${P.words_per_minute} wpm = ${words} words x ${P.chars_per_word.toFixed(1)} chars = ${voiceChars(P, minutes)} voice characters`,
  };
}
