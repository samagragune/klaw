import type { Metadata } from "next";
import { Providers } from "@/components/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Reliability",
  description: "The control plane for autonomous AI agents.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="bg-bg text-ink antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
