import { NextResponse } from "next/server";
import { z } from "zod";
import { apiUser, body, handler, owned } from "@/lib/api";

// Public-records (FOIA) log. Records requests; never promises footage the project does not have.
const Req = z.object({
  project_id: z.string().uuid(),
  id: z.string().uuid().optional(),
  request_number: z.string().max(80).optional().nullable(),
  request_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().nullable(),
  agency: z.string().min(2).max(200),
  records_requested: z.string().min(5).max(2000),
  status: z.enum(["drafted", "submitted", "acknowledged", "partial", "granted", "denied", "appealed", "closed"]),
  exemption_claimed: z.string().max(500).optional().nullable(),
  appeal_path: z.string().max(1000).optional().nullable(),
  notes: z.string().max(2000).optional().nullable(),
});

export const POST = handler(async (req: Request) => {
  const { sb } = await apiUser();
  const b = await body(req, Req);
  await owned(sb, "projects", b.project_id);
  const q = b.id ? sb.from("foia_requests").update(b).eq("id", b.id) : sb.from("foia_requests").insert(b);
  const { data, error } = await q.select().single();
  if (error) throw error;
  return NextResponse.json({ foia: data });
});
