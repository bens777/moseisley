import { ImageResponse } from "next/og";

export const alt = "Moseisley.sh — Your AI crew, working for you.";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OgImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "#0b0a08",
          backgroundImage:
            "linear-gradient(to right, rgba(237,230,218,0.04) 1px, transparent 1px), " +
            "linear-gradient(to bottom, rgba(237,230,218,0.04) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
          fontFamily: "monospace",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", fontSize: 88, fontWeight: 700 }}>
          <div
            style={{
              width: 0,
              height: 0,
              marginRight: 28,
              borderTop: "26px solid transparent",
              borderBottom: "26px solid transparent",
              borderLeft: "40px solid #e8a33d",
            }}
          />
          <span style={{ color: "#ede6da" }}>moseisley</span>
          <span style={{ color: "#e8a33d" }}>.sh</span>
        </div>
        <div style={{ marginTop: 28, fontSize: 34, color: "#9c948a" }}>
          Your AI crew, working for you.
        </div>
        <div
          style={{
            marginTop: 48,
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: 20,
            color: "#6b6359",
            textTransform: "uppercase",
            letterSpacing: 6,
          }}
        >
          <div style={{ width: 10, height: 10, borderRadius: 10, background: "#4fd8e0" }} />
          personal ai command center
        </div>
      </div>
    ),
    { ...size }
  );
}
