import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "YouTube AI Manager",
  description: "Autonomous YouTube Shorts creator and publisher",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-bg font-sans text-ink">{children}</body>
    </html>
  );
}
