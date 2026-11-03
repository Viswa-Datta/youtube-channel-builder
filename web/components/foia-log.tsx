"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

type Row = { id: string; request_number: string | null; request_date: string | null; agency: string; records_requested: string; status: string; exemption_claimed: string | null; appeal_path: string | null };

/** Public-records log: request number, date, agency, exemption claimed, appeal path. Never promises footage. */
export function FoiaLog({ projectId, rows, suggestions }: { projectId: string; rows: Row[]; suggestions: string[] }) {
  const router = useRouter();
  const [f, setF] = useState({ agency: "", records_requested: "", request_number: "", request_date: "", status: "drafted", exemption_claimed: "", appeal_path: "" });
  return (
    <Card>
      <CardHeader><CardTitle>FOIA / public-records log</CardTitle><CardDescription>Track requests for footage and records. A request is not footage: scenes stay marked as gaps until a release arrives and passes the rights check.</CardDescription></CardHeader>
      <CardContent className="space-y-3 text-sm">
        {suggestions.length > 0 && <ul className="list-disc pl-5 text-xs text-muted-foreground">{suggestions.map((s) => <li key={s}>{s}</li>)}</ul>}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-xs">
            <thead className="text-left text-muted-foreground"><tr><th>No.</th><th>Date</th><th>Agency</th><th>Records</th><th>Status</th><th>Exemption</th><th>Appeal path</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} className="border-t"><td className="py-1">{r.request_number}</td><td>{r.request_date}</td><td>{r.agency}</td><td>{r.records_requested}</td>
                <td><Badge variant="secondary">{r.status}</Badge></td><td>{r.exemption_claimed}</td><td>{r.appeal_path}</td></tr>
            ))}</tbody>
          </table>
        </div>
        <form className="grid gap-2 sm:grid-cols-3" onSubmit={async (e) => {
          e.preventDefault();
          const r = await fetch("/api/foia", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_id: projectId, ...f, request_date: f.request_date || null }) });
          if (r.ok) { setF({ ...f, agency: "", records_requested: "", request_number: "" }); router.refresh(); }
        }}>
          <Input required placeholder="Agency" value={f.agency} onChange={(e) => setF({ ...f, agency: e.target.value })} />
          <Input required placeholder="Records requested" value={f.records_requested} onChange={(e) => setF({ ...f, records_requested: e.target.value })} />
          <Input placeholder="Request number" value={f.request_number} onChange={(e) => setF({ ...f, request_number: e.target.value })} />
          <Input type="date" value={f.request_date} onChange={(e) => setF({ ...f, request_date: e.target.value })} />
          <Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
            {["drafted", "submitted", "acknowledged", "partial", "granted", "denied", "appealed", "closed"].map((s) => <option key={s}>{s}</option>)}
          </Select>
          <Input placeholder="Exemption claimed" value={f.exemption_claimed} onChange={(e) => setF({ ...f, exemption_claimed: e.target.value })} />
          <Input className="sm:col-span-2" placeholder="Appeal path (office, deadline)" value={f.appeal_path} onChange={(e) => setF({ ...f, appeal_path: e.target.value })} />
          <Button type="submit" variant="outline">Add to log</Button>
        </form>
      </CardContent>
    </Card>
  );
}
