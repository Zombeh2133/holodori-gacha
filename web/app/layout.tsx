import type { Metadata, Viewport } from "next";
import "./globals.css";

const SITE_URL = "https://holodorigacha.com";
const SITE_DESCRIPTION =
  "Track your hololive Dreams gacha pull history, banner stats, pity, and spark progress.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  applicationName: "Holodori Gacha",
  title: {
    default: "Holodori Gacha",
    template: "%s | Holodori Gacha",
  },
  description: SITE_DESCRIPTION,
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "Holodori Gacha",
    title: "Holodori Gacha",
    description: SITE_DESCRIPTION,
  },
  twitter: {
    card: "summary",
    title: "Holodori Gacha",
    description: SITE_DESCRIPTION,
  },
  robots: {
    index: true,
    follow: true,
  },
};

export const viewport: Viewport = {
  themeColor: "#090b12",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
