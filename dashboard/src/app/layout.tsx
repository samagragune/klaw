import type { Metadata } from "next";
import { Gloock, JetBrains_Mono, Schibsted_Grotesk } from "next/font/google";
import "./globals.css";
import { Ripples } from "@/components/ripples";

// Schibsted Grotesk: a newspaper grotesk for UI and body text.
const inter = Schibsted_Grotesk({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
// Gloock: a sharp, high-contrast editorial serif for display type.
const serif = Gloock({ subsets: ["latin"], weight: "400", variable: "--font-serif", display: "swap" });
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
