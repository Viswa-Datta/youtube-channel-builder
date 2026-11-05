import { ThemeToggle } from "@/components/theme-toggle";
import Link from "next/link";
import { LayoutDashboard, Lightbulb, Clapperboard, Wallet, Settings, Upload } from "lucide-react";
import { requireUser } from "@/lib/supabase/server";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/channels", label: "Channels", icon: Lightbulb },
  { href: "/projects", label: "Projects", icon: Clapperboard },
  { href: "/publish", label: "Publish", icon: Upload },
  { href: "/costs", label: "Costs", icon: Wallet },
  { href: "/settings", label: "Settings", icon: Settings },
];

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { user } = await requireUser();
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="hidden w-56 shrink-0 border-r bg-card md:block">
        <div className="p-4 font-serif text-lg">Channel Builder</div>
        <nav className="space-y-0.5 px-2">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-muted">
              <n.icon className="h-4 w-4" /> {n.label}
            </Link>
          ))}
        </nav>
        <div className="mt-6 truncate px-5 text-xs text-muted-foreground">{user.email}</div>
        <div className="mt-3 px-5"><ThemeToggle /></div>
      </aside>
      <main className="min-w-0 flex-1 p-3 pb-20 sm:p-6 md:pb-6">{children}</main>
      {/* phone navigation */}
      <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-6 border-t bg-card md:hidden">
        {NAV.map((n) => (
          <Link key={n.href} href={n.href} className="flex flex-col items-center gap-0.5 py-2 text-[10px]">
            <n.icon className="h-5 w-5" /> {n.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
