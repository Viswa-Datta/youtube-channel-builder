import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, HttpError } from "@/lib/api";
import { plansConfig } from "@/lib/pricing";

const Intake = z.object({
  niche: z.string().min(2).max(120),
  sub_niche: z.string().max(160).optional().default(""),
  audience: z.string().max(300).optional().default(""),
  language: z.string().min(2).max(10).default("en"),
  tone: z.string().max(200).optional().default(""),
  target_minutes: z.union([z.literal(8), z.literal(12), z.literal(20), z.literal(30), z.literal(45), z.literal(60)]),
  faceless: z.boolean().default(true),
  competitor_urls: z.array(z.string().url()).max(5).default([]),
});

export const POST = handler(async (req: Request) => {
  const { sb, user } = await apiUser();
  const input = await body(req, Intake);
  const { data: profile } = await sb.from("users").select("plan").eq("id", user.id).single();
  const plan = plansConfig.plans.find((p) => p.key === profile?.plan) ?? plansConfig.plans[0];
  const { count } = await sb.from("channels").select("id", { count: "exact", head: true });
  if ((count ?? 0) >= plan.channels) throw new HttpError(403, `Your ${plan.name} plan allows ${plan.channels} channel(s).`);
  const { data, error } = await sb.from("channels").insert({ ...input, user_id: user.id }).select("id").single();
  if (error) throw error;
  return NextResponse.json({ id: data.id });
});
