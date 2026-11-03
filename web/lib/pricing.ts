import P from "@/config/pricing.json";
import plans from "@/config/plans.json";
import { estimateWith, type Action, type Tier } from "./pricing-core";

export type { Action, Tier, Estimate, Line } from "./pricing-core";
export const pricing = P;
export const plansConfig = plans;

export function estimate(action: Action, tier: Tier, minutes = 12, scenes = 7) {
  return estimateWith(P, action, tier, minutes, scenes);
}

/** Provider/model the worker should use for a tier (payload for jobs). */
export function llmForTier(tier: Tier): { llm_provider: string; llm_model: string } {
  const [provider, ...rest] = (P.tiers[tier].llm as string).split(":");
  return { llm_provider: provider, llm_model: rest.join(":") };
}

export function ttsForTier(tier: Tier): { voice_provider: string; voice_id: string } {
  const key = P.tiers[tier].tts as string;
  const [provider, model] = key.split(":");
  const defaults: Record<string, string> = { kokoro: "am_michael", openai: "onyx", elevenlabs: "pNInz6obpgDQGcFmaJgB", google: "en-US-Chirp3-HD-Charon" };
  return { voice_provider: provider, voice_id: provider === "kokoro" ? model : defaults[provider] };
}
