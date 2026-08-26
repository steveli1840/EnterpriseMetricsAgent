import { describe, expect, it } from "vitest";
import { inferChartSpec } from "./chart";
import type { AnalysisResult } from "./api";

const baseEvidence: AnalysisResult["evidence"] = {
  metrics: [],
  schema_refs: [],
  knowledge_refs: [],
  filters: [],
  time_window: { start: "", end: "" },
  warehouse: "clickhouse",
  row_count: 2,
  elapsed_ms: 1,
  schema_snapshot: "olist-v1",
};

function result(partial: Partial<AnalysisResult>): AnalysisResult {
  return {
    answer: "ok",
    columns: [],
    result_preview: [],
    sql: { dialect: "clickhouse", statement: "SELECT 1", query_id: "q" },
    evidence: baseEvidence,
    trace_id: "t",
    warnings: [],
    ...partial,
  };
}

describe("inferChartSpec", () => {
  it("respects chart_hint.enabled=false", () => {
    expect(
      inferChartSpec(
        result({
          columns: ["database", "name", "total_rows"],
          result_preview: [
            ["raw_olist", "geolocation", 1000163],
            ["raw_olist", "orders", 99441],
          ],
          chart_hint: { enabled: false },
        }),
      ),
    ).toBeNull();
  });

  it("blocks legacy catalog results without hint", () => {
    expect(
      inferChartSpec(
        result({
          columns: ["database", "table", "name", "type", "position"],
          result_preview: [
            ["raw_olist", "geolocation", "lat", "Float64", 1],
            ["raw_olist", "geolocation", "lng", "Float64", 2],
          ],
        }),
      ),
    ).toBeNull();
  });

  it("charts when hint enabled", () => {
    const spec = inferChartSpec(
      result({
        columns: ["customer_state", "gmv"],
        result_preview: [
          ["SP", 100],
          ["RJ", 80],
          ["MG", 70],
        ],
        chart_hint: { enabled: true, type: "bar", x: "customer_state", y: "gmv" },
      }),
    );
    expect(spec?.type).toBe("bar");
    expect(spec?.categories).toEqual(["SP", "RJ", "MG"]);
  });
});
