/**
 * The line across the top of the app, above every view, when the project
 * could not be loaded: the reason and Retry, until a load gets the project.
 * It has no Dismiss: while it shows, what the views draw is not what the
 * server holds. A failed load is never shown as a failed save, which has its
 * own line (SaveFailedBanner).
 */
import { useProjectStore } from "../../store/projectStore";
import { bannerButtonStyle, bannerStyle, BANNER_TEXT_STYLE } from "./projectBannerStyles";

export function ProjectLoadFailedBanner() {
  const failure = useProjectStore((s) => s.loadError);
  const loading = useProjectStore((s) => s.loading);
  const retryLoad = useProjectStore((s) => s.retryLoad);
  return failure && (
    <div style={bannerStyle}>
      <span style={BANNER_TEXT_STYLE}>Could not load the project: {failure}</span>
      <button onClick={() => retryLoad()} disabled={loading} style={bannerButtonStyle.strong}>
        Retry
      </button>
    </div>
  );
}
