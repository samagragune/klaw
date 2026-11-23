import type { Metadata } from "next";
import { Inter, Instrument_Serif, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Ripples } from "@/components/ripples";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
// Editorial serif voice for display type.
const serif = Instrument_Serif({ subsets: ["latin"], weight: "400", variable: "--font-serif", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains", display: "swap" });

export const metadata: Metadata = {
  title: "Klaw — the supervisor for AI agents",
  description:
    "Run a supervisor on your own machine, connect Claude Code and OpenAI Agents SDK agents, and watch, approve and stop their work from one dashboard.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${serif.variable} ${mono.variable}`}>
      <body className="text-ink antialiased">
        <Ripples />
        {children}
      </body>
    </html>
  );
}
