/**
 * The Theme Studio's Contrast (WCAG) section: each of `themeContrast.ts`'s pairs
 * on a row, with its two colours, its ratio and its rating, and the number
 * failing in the title. How a rating looks (its icon, the colour of its ratio,
 * the word beside it) is one row of `LEVELS`.
 */
import { useMemo } from "react";
import { AlertTriangle, Check, Minus, type LucideIcon } from "lucide-react";
import type { WcagLevel } from "./colorUtils";
import { contrastChecks, type ContrastCheck } from "./themeContrast";
import { StudioSection } from "./StudioParts";

const FAIL = "#ef5350";
const PASS = "#66bb6a";
const MUTED = "var(--text-muted)";

const LEVELS: Record<WcagLevel, { Icon: LucideIcon; iconColor: string; ratioColor: string; word: string }> = {
  fail: { Icon: AlertTriangle, iconColor: FAIL, ratioColor: FAIL, word: "fail" },
  na: { Icon: Minus, iconColor: MUTED, ratioColor: MUTED, word: "n/a" },
  AA: { Icon: Check, iconColor: PASS, ratioColor: "#ffa726", word: "AA" },
  AAA: { Icon: Check, iconColor: PASS, ratioColor: PASS, word: "AAA" },
};

const swatch = (background: string) => ({
  width: 12,
  height: 12,
  borderRadius: 2,
  background,
  border: "1px solid rgba(128,128,128,0.3)",
});

function ContrastRow({ check }: { check: ContrastCheck }) {
  const { Icon, iconColor, ratioColor, word } = LEVELS[check.level];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
      <Icon size={12} style={{ color: iconColor, flexShrink: 0 }} />
      <span style={{ flex: 1, color: "var(--text-secondary)" }}>{check.label}</span>
      <div style={{ display: "flex", gap: 2 }}>
        <div style={swatch(check.fg)} />
        <div style={swatch(check.bg)} />
      </div>
      <span
        style={{
          fontFamily: "monospace",
          fontSize: 10,
          fontWeight: 600,
          color: ratioColor,
          width: 56,
          textAlign: "right",
        }}
      >
        {check.ratio ? `${check.ratio.toFixed(1)}:1` : "—"}{" "}
        <span style={{ fontSize: 8 }}>{word}</span>
      </span>
    </div>
  );
}

export function ContrastSection({ variables }: { variables: Record<string, unknown> }) {
  const checks = useMemo(() => contrastChecks(variables), [variables]);
  const failing = checks.filter((c) => c.level === "fail").length;
  return (
    <StudioSection
      title={
        <>
          Contrast (WCAG)
          {failing > 0 && (
            <span style={{ color: FAIL, marginLeft: 6, fontWeight: 600 }}>
              {failing} failing
            </span>
          )}
        </>
      }
    >
      <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 4 }}>
        {checks.map((c) => (
          <ContrastRow key={c.label} check={c} />
        ))}
        <div style={{ fontSize: 10, color: MUTED, marginTop: 6, lineHeight: 1.4 }}>
          WCAG AA requires 4.5:1 for normal text. AAA requires 7:1.
        </div>
      </div>
    </StudioSection>
  );
}
