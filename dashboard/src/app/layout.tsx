import type { Metadata } from "next";
import { Instrument_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Ripples } from "@/components/ripples";

// Instrument Sans: a grotesk with a technical edge (the dev-tool category runs
// 79% grotesk); variable weight for light display and firm UI text.
const inter = Instrument_Sans({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains", display: "swap" });

export const metadata: Metadata = {
  title: "Klaw — the supervisor for AI agents",
  description:
    "Run a supervisor on your own machine, connect Claude Code and OpenAI Agents SDK agents, and watch, approve and stop their work from one dashboard.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="text-ink antialiased">
        <Ripples />
        {children}
      </body>
    </html>
  );
}
