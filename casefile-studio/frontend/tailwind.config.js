/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "#0F1117",
        surface: "#1A1D29",
        raised: "#232735",
        magenta: "#FF2D75",
        violet: "#7C3AED",
        amber: "#FFB020",
        info: "#22D3EE",
        success: "#22C55E",
        danger: "#EF4444",
      },
      fontFamily: {
        display: ["Space Grotesk", "Segoe UI", "system-ui", "sans-serif"],
        body: ["Inter", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Consolas", "monospace"],
      },
      borderRadius: { "2xl": "1rem" },
      boxShadow: {
        glow: "0 0 40px -12px rgba(255,45,117,0.45)",
        card: "0 10px 30px -12px rgba(0,0,0,0.6)",
      },
      backgroundImage: {
        accent: "linear-gradient(135deg,#FF2D75 0%,#7C3AED 100%)",
      },
    },
  },
  plugins: [],
};
