import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Moseisley.sh",
    short_name: "Moseisley.sh",
    description:
      "Your AI crew, working for you. Give your agents goals, tools and autonomy — Moseisley.sh coordinates the rest.",
    start_url: "/manager",
    display: "standalone",
    background_color: "#0b0a08",
    theme_color: "#0b0a08",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }],
  };
}
