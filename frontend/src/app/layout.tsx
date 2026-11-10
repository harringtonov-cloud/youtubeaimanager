import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "YouTube AI Manager",
  description: "Autonomous YouTube Shorts creator and publisher",
  robots: { index: false, follow: false },
  // browser auto-translate rewrites text nodes and crashes React ("insertBefore" error)
  other: { google: "notranslate" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" translate="no" className="dark notranslate">
      <body className="min-h-screen bg-bg font-sans text-ink">{children}</body>
    </html>
  );
}
