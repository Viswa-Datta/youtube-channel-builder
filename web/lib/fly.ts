/**
 * Wakes the Fly worker when there is work. The worker turns itself off after a few idle minutes, so Fly only bills
 * while videos are being made. Needs FLY_API_TOKEN (a deploy token for the worker app) and FLY_APP in Vercel; without
 * them this does nothing (an always-on worker keeps working as before).
 */
const API = "https://api.machines.dev/v1";
let lastWake = 0;

export async function wakeWorker(): Promise<void> {
  const token = process.env.FLY_API_TOKEN, app = process.env.FLY_APP;
  if (!token || !app) return;
  if (Date.now() - lastWake < 20_000) return; // one wake call per 20 s per server instance is plenty
  lastWake = Date.now();
  try {
    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    const r = await fetch(`${API}/apps/${app}/machines`, { headers, cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!r.ok) return;
    const machines = (await r.json()) as { id: string; state: string }[];
    await Promise.all(machines.filter((m) => m.state === "stopped" || m.state === "suspended").map((m) =>
      fetch(`${API}/apps/${app}/machines/${m.id}/start`, { method: "POST", headers, signal: AbortSignal.timeout(8000) }).catch(() => null)));
  } catch {
    // Waking is best effort: the job stays queued and the next page refresh tries again.
  }
}
