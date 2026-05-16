import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ResolveAI · Adversarially-Hardened Multi-Agent Customer Support",
  description: "Sierra/Decagon-style multi-agent customer support with 4-layer guardrails.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
