import type { Metadata } from "next";
import { Inter } from "next/font/google";

import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "OpenVox — Self-hosted voice agent platform",
  description:
    "Build, test, and deploy voice agents with BytePlus, ElevenLabs, OpenAI, Anthropic and more. Runs on your machine — no cloud middle-man.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${inter.variable}`}>
      <body className="font-sans">{children}</body>
    </html>
  );
}
