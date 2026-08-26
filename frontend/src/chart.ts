import type { AnalysisResult } from "./api";

export type ChartSpec = {
  type: "bar" | "line";
  categories: string[];
  values: number[];
  xLabel: string;
  yLabel: string;
};

function isNumeric(value: unknown): boolean {
  if (typeof value === "number" && Number.isFinite(value)) return true;
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) return true;
  return false;
}

function toNumber(value: unknown): number {
  return typeof value === "number" ? value : Number(value);
}

/** Word-boundary-ish time hints — avoid matching "at" inside "database". */
const TIME_RE = /(^|_)(month|date|day|week|year|time|at)(_|$)/i;

const META_Y_BLOCKLIST = new Set([
  "position",
  "ordinal",
  "ordinal_position",
  "id",
  "type",
  "database",
  "table",
  "name",
  "engine",
  "uuid",
]);

function uniqueCount(values: string[]): number {
  return new Set(values).size;
}

function buildSpec(
  result: AnalysisResult,
  xIdx: number,
  yIdx: number,
  preferredType?: "bar" | "line" | null,
): ChartSpec | null {
  const sample = result.result_preview.slice(0, 20);
  if (!sample.every(row => isNumeric(row[yIdx]))) return null;

  const xLabel = result.columns[xIdx];
  const yLabel = result.columns[yIdx];
  if (META_Y_BLOCKLIST.has(yLabel.toLowerCase())) return null;

  const categories = result.result_preview.map(row => String(row[xIdx] ?? ""));
  const values = result.result_preview.map(row => toNumber(row[yIdx]));
  if (uniqueCount(categories) < 2) return null;

  const looksTemporal = TIME_RE.test(xLabel);
  let cats = categories;
  let vals = values;
  if (!looksTemporal && cats.length > 30) {
    const ranked = cats
      .map((cat, index) => ({ cat, val: vals[index] }))
      .sort((a, b) => b.val - a.val)
      .slice(0, 20);
    cats = ranked.map(item => item.cat);
    vals = ranked.map(item => item.val);
  }

  return {
    type: preferredType ?? (looksTemporal ? "line" : "bar"),
    categories: cats,
    values: vals,
    xLabel,
    yLabel,
  };
}

/**
 * Prefer backend chart_hint from chart_planner.
 * Only a narrow legacy fallback remains when hint is missing.
 */
export function inferChartSpec(result: AnalysisResult): ChartSpec | null {
  if (result.columns.length < 2 || result.result_preview.length < 2) return null;

  const hint = result.chart_hint;
  if (hint && hint.enabled === false) return null;

  if (hint?.enabled) {
    const xIdx = hint.x ? result.columns.indexOf(hint.x) : 0;
    const yIdx = hint.y ? result.columns.indexOf(hint.y) : result.columns.length - 1;
    if (xIdx < 0 || yIdx < 0) return null;
    const preferred =
      hint.type === "line" || hint.type === "bar" ? hint.type : null;
    return buildSpec(result, xIdx, yIdx, preferred);
  }

  // Legacy sessions without chart_hint: never invent charts for catalog-shaped results.
  const lower = result.columns.map(column => column.toLowerCase());
  if (lower.includes("position") || (lower.includes("database") && lower.includes("name"))) {
    return null;
  }
  return null;
}
