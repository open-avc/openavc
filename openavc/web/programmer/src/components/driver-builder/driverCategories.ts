// The canonical driver categories the community catalog accepts, with the
// labels the authoring dropdowns show. The category ids come from the
// platform's generated contract tables (DRIVER_CATEGORY_IDS in
// types.gen.ts), so a value outside the catalog's list is a compile error
// here rather than a rejection at catalog-submission CI, far from the
// authoring surface. Labels are IDE presentation and are maintained here.
import { DRIVER_CATEGORY_IDS } from "../../api/types";

export type CatalogCategoryId = (typeof DRIVER_CATEGORY_IDS)[number];

export interface DriverCategory {
  value: CatalogCategoryId;
  label: string;
}

export const DRIVER_CATEGORIES: DriverCategory[] = [
  { value: "projector", label: "Projector" },
  { value: "display", label: "Display" },
  { value: "switcher", label: "Switcher" },
  { value: "audio", label: "Audio" },
  { value: "camera", label: "Camera" },
  { value: "video", label: "Video (encoders, decoders, NDI)" },
  { value: "streaming", label: "Streaming" },
  { value: "lighting", label: "Lighting" },
  { value: "power", label: "Power (PDU, UPS, sequencer)" },
  { value: "utility", label: "Utility" },
];
