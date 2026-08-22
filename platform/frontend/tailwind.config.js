/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0B1220",
        surface: "#121A2B",
        raised: "#182338",
        border: "#223049",
        ink: "#E6EDF5",
        muted: "#8593A8",
        amber: "#F2A94D",
        cyan: "#4DD2F2",
        good: "#4CAF7D",
        bad: "#E2574C",
      },
      fontFamily: {
        mono: ["'IBM Plex Mono'", "ui-monospace", "SFMono-Regular", "monospace"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        glow: "0 0 0 3px rgba(77,210,242,0.15)",
      },
    },
  },
  plugins: [],
};
