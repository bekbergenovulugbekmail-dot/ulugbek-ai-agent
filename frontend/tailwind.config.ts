import type { Config } from "tailwindcss";

/**
 * ULUGBEK AI design tokens.
 *
 * One accent, semantic status colours, and nothing else — an operations console
 * earns its clarity from hierarchy and spacing, not from colour.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#08080A",
        surface: "#0E0E12",
        elevated: "#14141A",
        overlay: "#1A1A21",
        line: "rgba(255,255,255,0.07)",
        "line-strong": "rgba(255,255,255,0.13)",
        ink: {
          DEFAULT: "#EDEDEF",
          muted: "#A0A0AB",
          faint: "#6B6B76",
        },
        accent: {
          DEFAULT: "#5B7CFA",
          soft: "rgba(91,124,250,0.14)",
          line: "rgba(91,124,250,0.35)",
        },
        ok: { DEFAULT: "#34D399", soft: "rgba(52,211,153,0.13)" },
        warn: { DEFAULT: "#FBBF24", soft: "rgba(251,191,36,0.13)" },
        danger: { DEFAULT: "#FB7185", soft: "rgba(251,113,133,0.13)" },
        info: { DEFAULT: "#38BDF8", soft: "rgba(56,189,248,0.13)" },
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.04em" }],
      },
      borderRadius: { xl: "0.875rem", "2xl": "1.125rem" },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.22s ease-out both",
        "pulse-soft": "pulse-soft 1.8s ease-in-out infinite",
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [],
};

export default config;
