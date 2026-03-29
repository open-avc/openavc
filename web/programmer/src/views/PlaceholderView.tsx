import { Construction } from "lucide-react";
import { ViewContainer } from "../components/layout/ViewContainer";

interface PlaceholderViewProps {
  title: string;
  description?: string;
}

export function PlaceholderView({
  title,
  description = "This view is coming in a future update.",
}: PlaceholderViewProps) {
  return (
    <ViewContainer title={title}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "60%",
          color: "var(--text-muted)",
          gap: "var(--space-lg)",
        }}
      >
        <Construction size={48} />
        <h2 style={{ fontSize: "var(--font-size-xl)", color: "var(--text-secondary)" }}>
          Coming Soon
        </h2>
        <p style={{ maxWidth: 400, textAlign: "center" }}>{description}</p>
      </div>
    </ViewContainer>
  );
}
