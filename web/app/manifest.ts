import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Holodori Gacha",
    short_name: "Holodori Gacha",
    description:
      "Track your hololive Dreams gacha pull history, banner stats, pity, and spark progress.",
    start_url: "/",
    display: "standalone",
    background_color: "#090b12",
    theme_color: "#090b12",
    icons: [
      {
        src: "/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
      },
    ],
  };
}
