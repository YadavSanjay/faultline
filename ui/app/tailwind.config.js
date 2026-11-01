/** @type {import('tailwindcss').Config} */

// Semantic color role → CSS variable (defined in src/index.css). Channels are
// stored space-separated so Tailwind's <alpha-value> opacity modifiers work
// (e.g. bg-surface-2/60). Components must reference these role names, never a
// raw slate-* shade — that is what keeps the system consistent by construction.
const withAlpha = (v) => `rgb(var(${v}) / <alpha-value>)`;

export default {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        // structural neutrals
        surface: {
          0: withAlpha("--surface-0"),
          1: withAlpha("--surface-1"),
          2: withAlpha("--surface-2"),
          code: withAlpha("--surface-code"),
        },
        border: {
          DEFAULT: withAlpha("--border"),
          subtle: withAlpha("--border-subtle"),
          strong: withAlpha("--border-strong"),
        },
        // text roles: text-fg / text-fg-secondary / text-fg-muted / text-fg-inverse
        fg: {
          DEFAULT: withAlpha("--text-primary"),
          secondary: withAlpha("--text-secondary"),
          muted: withAlpha("--text-muted"),
          inverse: withAlpha("--text-inverse"),
          code: withAlpha("--fg-code"),
        },
        // primary action / active / link
        primary: {
          DEFAULT: withAlpha("--primary"),
          hover: withAlpha("--primary-hover"),
          tint: withAlpha("--primary-tint"),
          fg: withAlpha("--on-primary"),
        },
        // muted semantics (status, confidence, gain/loss). 3 stops: fg = legible
        // text, DEFAULT = dots/marks/borders, tint = fills.
        success: { DEFAULT: withAlpha("--success"), fg: withAlpha("--success-fg"), tint: withAlpha("--success-tint") },
        warning: { DEFAULT: withAlpha("--warning"), fg: withAlpha("--warning-fg"), tint: withAlpha("--warning-tint") },
        danger: { DEFAULT: withAlpha("--danger"), fg: withAlpha("--danger-fg"), tint: withAlpha("--danger-tint") },
        info: { DEFAULT: withAlpha("--info"), fg: withAlpha("--info-fg"), tint: withAlpha("--info-tint") },
        accent: withAlpha("--accent"),

        // categorical identity palette (module / RAG-service tags — NOT status).
        // Use via the <CategoryTag> component / categoryClass() helper.
        cat: {
          teal: { fg: withAlpha("--cat-teal-fg"), tint: withAlpha("--cat-teal-tint") },
          indigo: { fg: withAlpha("--cat-indigo-fg"), tint: withAlpha("--cat-indigo-tint") },
          violet: { fg: withAlpha("--cat-violet-fg"), tint: withAlpha("--cat-violet-tint") },
        },

        // NOTE: legacy `zone` tokens intentionally removed — they were defined
        // but never referenced; Zone.tsx is migrated onto success/warning/danger
        // in P1. Status uses the semantic roles above from here on.
      },
      // Named type roles (see rag-ui-design type scale). Migration onto these
      // happens in the P3 sweep; adding them here is additive / a no-op for now.
      fontSize: {
        // dense micro-labels (tags, dense tables, heatmap cells). A financial UI
        // legitimately needs sub-12px; these name the two sizes the app actually
        // uses so they stop being arbitrary text-[9px]/[10px]/[11px] brackets.
        nano: ["10px", { lineHeight: "14px" }],
        micro: ["11px", { lineHeight: "16px" }],
        caption: ["12px", { lineHeight: "16px", fontWeight: "500" }],
        "body-sm": ["13px", { lineHeight: "20px" }],
        body: ["15px", { lineHeight: "24px" }],
        subtitle: ["18px", { lineHeight: "26px", fontWeight: "600" }],
        title: ["22px", { lineHeight: "30px", fontWeight: "600" }],
        display: ["30px", { lineHeight: "38px", fontWeight: "700" }],
      },
      // One radius language: sm chips/inputs, md cards/buttons, lg panels/modals.
      borderRadius: {
        "ui-sm": "6px",
        "ui-md": "10px",
        "ui-lg": "14px",
      },
      boxShadow: {
        "ui-sm": "0 1px 2px rgb(20 23 28 / 0.06)",
        "ui-md": "0 4px 12px rgb(20 23 28 / 0.08)",
      },
    },
  },
  plugins: [],
};
