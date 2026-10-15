import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Holodori Gacha",
  description: "Holodori gacha history, pity, and pull statistics.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
