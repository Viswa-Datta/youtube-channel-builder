import Link from "next/link";

export default function LegalLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-3xl p-4 sm:p-8">
      <nav className="mb-6 flex gap-4 text-sm"><Link href="/">Home</Link><Link href="/legal/privacy">Privacy</Link><Link href="/legal/terms">Terms</Link><Link href="/legal/data-deletion">Data deletion</Link></nav>
      <article className="space-y-3 text-sm leading-relaxed [&_h1]:font-serif [&_h1]:text-2xl [&_h2]:mt-5 [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc">{children}</article>
      <p className="mt-8 text-xs text-muted-foreground">Template text. Have it reviewed by a lawyer for your jurisdiction before launch; replace the operator details.</p>
    </main>
  );
}
