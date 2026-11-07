/**
 * Publishing slots: fixed weekdays at a fixed local time in the channel's time zone (e.g. Tue + Sat, 1:00 PM
 * America/Chicago). Each finished video takes the next free slot. Pure functions, no dependencies.
 */
export const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"] as const;
export const DAY_LABEL: Record<string, string> = { sun: "Sun", mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat" };

export type Plan = { days: string[]; time: string; tz: string };

/** Offset (ms) of `tz` from UTC at instant `t`. */
function tzOffset(t: number, tz: string): number {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(new Date(t)).map((x) => [x.type, x.value]),
  );
  return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - Math.floor(t / 1000) * 1000;
}

/** The UTC instant of a wall-clock time in `tz` (handles daylight-saving changes). */
export function zonedTime(y: number, m: number, d: number, hh: number, mm: number, tz: string): Date {
  const guess = Date.UTC(y, m, d, hh, mm);
  let t = guess - tzOffset(guess, tz);
  t = guess - tzOffset(t, tz);
  return new Date(t);
}

/** Calendar date (y, m, d, weekday) of instant `t` in `tz`. */
function localDate(t: number, tz: string) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" })
      .formatToParts(new Date(t)).map((x) => [x.type, x.value]),
  );
  return { y: +p.year, m: +p.month - 1, d: +p.day, wd: (p.weekday as string).slice(0, 3).toLowerCase() };
}

/** Upcoming slots, starting at least `leadHours` from now. */
export function upcomingSlots(plan: Plan, count = 12, from = Date.now(), leadHours = 3): Date[] {
  const [hh, mm] = plan.time.split(":").map(Number);
  const days = plan.days.length ? plan.days : ["tue", "sat"];
  const out: Date[] = [];
  for (let k = 0; out.length < count && k < 120; k++) {
    const ld = localDate(from + k * 86_400_000, plan.tz);
    if (!days.includes(ld.wd)) continue;
    const slot = zonedTime(ld.y, ld.m, ld.d, hh, mm, plan.tz);
    if (slot.getTime() >= from + leadHours * 3_600_000 && !out.some((s) => s.getTime() === slot.getTime())) out.push(slot);
  }
  return out;
}

/** The next slot no other video of the channel has taken (taken = planned or already posted on that day). */
export function nextFreeSlot(plan: Plan, taken: (string | Date)[], from = Date.now()): Date {
  const takenDays = new Set(taken.map((t) => { const d = localDate(new Date(t).getTime(), plan.tz); return `${d.y}-${d.m}-${d.d}`; }));
  for (const s of upcomingSlots(plan, 60, from)) {
    const d = localDate(s.getTime(), plan.tz);
    if (!takenDays.has(`${d.y}-${d.m}-${d.d}`)) return s;
  }
  return upcomingSlots(plan, 1, from)[0];
}

export function formatSlot(d: Date | string, tz: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "long", month: "long", day: "numeric", hour: "numeric",
    minute: "2-digit", timeZoneName: "short" }).format(new Date(d));
}

/** Value for <input type="datetime-local"> showing the instant in `tz`. */
export function toLocalInput(d: Date | string, tz: string): string {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit" }).formatToParts(new Date(d)).map((x) => [x.type, x.value]),
  );
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

export function fromLocalInput(v: string, tz: string): Date {
  const [date, time] = v.split("T");
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  return zonedTime(y, m - 1, d, hh, mm, tz);
}

/**
 * Final description for YouTube: the hook paragraph, then "Watch next" links to the channel's latest posted videos and
 * a subscribe link, then the rest (chapters, sources, disclosure). Links only appear when they exist.
 */
export function finalDescription(description: string, watchNext: { title: string; url: string }[], channelUrl: string | null): string {
  const [hook, ...rest] = (description ?? "").split(/\n\s*\n/);
  const block: string[] = [];
  if (watchNext.length) block.push(...watchNext.slice(0, 3).map((v) => `▶ Watch next: ${v.title}\n${v.url}`));
  if (channelUrl) block.push(`🔔 Subscribe for more: ${channelUrl.replace(/\/+$/, "")}?sub_confirmation=1`);
  // YouTube: max 5,000 characters, no angle brackets.
  return [hook, ...(block.length ? [block.join("\n\n")] : []), ...rest].filter((x) => x?.trim()).join("\n\n")
    .replace(/</g, "(").replace(/>/g, ")").slice(0, 5000);
}

/** YouTube allows up to 500 characters of tags in total. */
export function tagString(tags: string[] | null): string {
  const out: string[] = [];
  let n = 0;
  for (const t of tags ?? []) {
    const c = t.replace(/[<>,]/g, "").trim();
    if (!c || n + c.length + 2 > 480) continue;
    out.push(c);
    n += c.length + 2;
  }
  return out.join(", ");
}
