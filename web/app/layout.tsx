import type { Metadata, Viewport } from "next";
import { Analytics } from "@vercel/analytics/react";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "YouTube Channel Builder", template: "%s · YouTube Channel Builder" },
  description: "Plan, research, script, voice, edit and publish long-form YouTube documentaries — with sourced research, real-footage rights tracking, privacy blur and cost shown before every step.",
  robots: { index: true, follow: true },
  openGraph: { title: "YouTube Channel Builder", type: "website" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#151a24" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">
        {children}
        <Analytics />
      </body>
    </html>
  );
}
