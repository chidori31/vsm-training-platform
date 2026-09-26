import { describe, expect, it } from "vitest";
import { parseAnalytics, parseDebrief } from "./contracts";
import { analyticsFixture, debriefFixture } from "./testFixtures";

describe("learning read model contracts", () => {
  it("accepts server effects including negative, unchanged and clamped values", () => {
    const parsed = parseDebrief(debriefFixture());
    expect(parsed.decisions[0].loyalty).toMatchObject({
      delta: 5,
      requested_delta: 10,
    });
    expect(parsed.decisions[0].safety.delta).toBe(0);
    expect(parsed.decisions[0].alternatives[1].safety_delta).toBe(-10);
  });
  it.each([
    (value: ReturnType<typeof debriefFixture>) => {
      value.decisions[0].elapsed_seconds = NaN;
    },
    (value: ReturnType<typeof debriefFixture>) => {
      value.decisions[0].safety.explanation = "";
    },
    (value: ReturnType<typeof debriefFixture>) => {
      value.decisions[0].suggestion.choice_ids = ["restricted"];
    },
    (value: ReturnType<typeof debriefFixture>) => {
      value.decisions[0].loyalty.delta = Infinity;
    },
    (value: ReturnType<typeof debriefFixture>) => {
      value.completed_at = "yesterday";
    },
    (value: ReturnType<typeof debriefFixture>) => {
      value.rule_version = 2;
    },
  ])(
    "rejects malformed debrief data before it reaches the timeline (%#)",
    (mutate) => {
      const value = debriefFixture();
      mutate(value);
      expect(() => parseDebrief(value)).toThrow(/некорректный/);
    },
  );
  it("preserves supplied competency conclusions and nullable scenario averages", () => {
    const parsed = parseAnalytics(analyticsFixture());
    expect(parsed.competencies[2].status).toBe("insufficient_data");
    expect(parsed.scenarios[1].average_loyalty).toBeNull();
  });
  it.each([
    (value: ReturnType<typeof analyticsFixture>) => {
      value.competencies[0].status = "expert";
    },
    (value: ReturnType<typeof analyticsFixture>) => {
      value.competencies[0].trend[0].completed_at = "invalid";
    },
    (value: ReturnType<typeof analyticsFixture>) => {
      value.patterns[0].count = -1;
    },
    (value: ReturnType<typeof analyticsFixture>) => {
      value.scenarios[0].average_duration_seconds = NaN;
    },
    (value: ReturnType<typeof analyticsFixture>) => {
      value.strengths = ["missing"];
    },
  ])(
    "rejects malformed analytics instead of fabricating conclusions (%#)",
    (mutate) => {
      const value = analyticsFixture();
      mutate(value);
      expect(() => parseAnalytics(value)).toThrow(/некорректный/);
    },
  );
});
