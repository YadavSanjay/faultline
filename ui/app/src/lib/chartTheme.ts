import type { Theme } from "./theme";

// ECharts theme derived from the design tokens (index.css). Charts live outside
// Tailwind, so without this their axes/labels/legend use ECharts' default
// dark-on-light styling and become invisible in dark mode. Values mirror the
// token hexes for the two themes; kept here (not read via getComputedStyle) so
// chart init never races the ThemeProvider setting data-theme.

// Calm, mutually-distinct categorical series palette (asset classes, factors,
// curves…). One palette, theme-aware — dataviz consistency by construction.
const PALETTE: Record<Theme, string[]> = {
  light: ["#4E9C93", "#4C77B0", "#7E6FBE", "#C29350", "#C57391", "#6BA36E", "#A46FB0"],
  dark: ["#6FC0B6", "#7BA3D8", "#A99AE0", "#DAB679", "#E09BB2", "#93C896", "#C79AD0"],
};

const STRUCTURE: Record<
  Theme,
  { axisLabel: string; legend: string; line: string; split: string; tipBg: string; tipBorder: string; tipText: string }
> = {
  light: {
    axisLabel: "#8A93A3", // --text-muted
    legend: "#5A6472", // --text-secondary
    line: "#E2E6EC", // --border
    split: "#EEF1F5", // --surface-2
    tipBg: "#FFFFFF", // --surface-1
    tipBorder: "#E2E6EC",
    tipText: "#1E2530", // --text-primary
  },
  dark: {
    axisLabel: "#727B8A",
    legend: "#A6AEBC",
    line: "#2E3542",
    split: "#232833",
    tipBg: "#1B1F26",
    tipBorder: "#2E3542",
    tipText: "#E6E9EF",
  },
};

export function chartPalette(theme: Theme): string[] {
  return PALETTE[theme];
}

// Semantic chart colors (mirror the semantic tokens) for series whose color
// carries meaning — P&L sign, VaR/ES, limits, exceptions.
const SEMANTIC = {
  light: { pos: "#4F9D7B", neg: "#C56B6B", neutral: "#8A93A3", warning: "#C9A24B", info: "#4C77B0", accent: "#7E6FBE", ink: "#1E2530", surface: "#FFFFFF" },
  dark: { pos: "#6FB694", neg: "#D08C8C", neutral: "#727B8A", warning: "#D8BC72", info: "#7BA3D8", accent: "#A99AE0", ink: "#E6E9EF", surface: "#1B1F26" },
} as const;

export function chartColors(theme: Theme) {
  return SEMANTIC[theme];
}

// Diverging scale for heatmaps: negative → mid → positive. Midpoint is the
// surface color (not white) so zero blends into the theme instead of glaring.
export function divergingScale(theme: Theme): [string, string, string] {
  return theme === "dark"
    ? [SEMANTIC.dark.neg, "#232833", SEMANTIC.dark.info]
    : [SEMANTIC.light.neg, "#EEF1F5", SEMANTIC.light.info];
}

/** Full ECharts theme object for echarts.registerTheme / init(el, name). */
export function chartTheme(theme: Theme): object {
  const s = STRUCTURE[theme];
  return {
    color: PALETTE[theme],
    textStyle: { color: s.legend },
    legend: { textStyle: { color: s.legend }, inactiveColor: s.axisLabel },
    categoryAxis: {
      axisLine: { lineStyle: { color: s.line } },
      axisTick: { lineStyle: { color: s.line } },
      axisLabel: { color: s.axisLabel },
      splitLine: { show: false, lineStyle: { color: s.split } },
    },
    valueAxis: {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: s.axisLabel },
      splitLine: { show: true, lineStyle: { color: s.split, type: "dashed" } },
    },
    tooltip: {
      backgroundColor: s.tipBg,
      borderColor: s.tipBorder,
      textStyle: { color: s.tipText },
    },
  };
}
