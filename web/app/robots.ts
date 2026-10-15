import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
    },
    sitemap: "https://holodorigacha.com/sitemap.xml",
    host: "https://holodorigacha.com",
  };
}
