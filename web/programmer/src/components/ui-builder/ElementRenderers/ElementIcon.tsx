import * as LucideIcons from "lucide-react";

interface ElementIconProps {
  name: string;
  size?: number;
  color?: string;
}

function getIconComponent(kebabName: string): React.ComponentType<{ size?: number; color?: string }> | null {
  const pascal = kebabName
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
  // Use the icons namespace (resilient to tree-shaking), fall back to direct named export
  const icons = (LucideIcons as Record<string, unknown>).icons as Record<string, unknown> | undefined;
  const comp = icons?.[pascal] || (LucideIcons as Record<string, unknown>)[pascal];
  // Lucide icons are forwardRef objects (typeof "object" with render), not plain functions
  if (comp && (typeof comp === "function" || typeof (comp as { render?: unknown }).render === "function")) {
    return comp as React.ComponentType<{ size?: number; color?: string }>;
  }
  return null;
}

export function ElementIcon({ name, size, color }: ElementIconProps) {
  size = size ?? 24;
  if (!name) return null;

  // Custom asset icon
  if (name.startsWith("assets://")) {
    return (
      <img
        src={`/api/projects/default/assets/${name.slice("assets://".length)}`}
        style={{ width: size, height: size, flexShrink: 0 }}
        alt=""
      />
    );
  }

  // Lucide icon
  const Comp = getIconComponent(name);
  if (!Comp) return null;

  return <Comp size={size} color={color || "currentColor"} />;
}

interface IconTextLayoutProps {
  icon?: string;
  iconPosition?: string;
  iconSize?: number;
  iconColor?: string;
  children: React.ReactNode;
}

export function IconTextLayout({
  icon,
  iconPosition = "left",
  iconSize,
  iconColor,
  children,
}: IconTextLayoutProps) {
  if (!icon) return <>{children}</>;

  const iconEl = <ElementIcon name={icon} size={iconSize ?? 24} color={iconColor} />;

  if (iconPosition === "center") {
    return iconEl;
  }

  const isVertical = iconPosition === "top" || iconPosition === "bottom";
  const style: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: isVertical ? 4 : 6,
    flexDirection: isVertical
      ? "column"
      : "row",
    width: "100%",
    height: "100%",
  };

  if (iconPosition === "right" || iconPosition === "bottom") {
    return (
      <div style={style}>
        <span>{children}</span>
        {iconEl}
      </div>
    );
  }

  return (
    <div style={style}>
      {iconEl}
      <span>{children}</span>
    </div>
  );
}
