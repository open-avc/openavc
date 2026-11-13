/**
 * The line across the top of the app, above every view, when the last save
 * failed: the reason, Retry (a direct save) and Dismiss (which clears the
 * failure). It stays away for a version conflict, which has its own banner
 * (store/saveState.ts, which the UI Builder toolbar's status word follows
 * too). A failed project load is not a failed save and has its own line
 * (ProjectLoadFailedBanner).
 */
import { useProjectStore } from "../../store/projectStore";
import { saveFailure } from "../../store/saveState";
import { bannerButtonStyle, bannerStyle, BANNER_TEXT_STYLE } from "./projectBannerStyles";

export function SaveFailedBanner() {
  const failure = useProjectStore(saveFailure);
  const save = useProjectStore((s) => s.save);
  return failure && (
    <div style={bannerStyle}>
      <span style={BANNER_TEXT_STYLE}>Save failed: {failure}</span>
      <button onClick={() => save()} style={bannerButtonStyle.strong}>
        Retry
      </button>
      <button onClick={() => useProjectStore.setState({ error: null })} style={bannerButtonStyle.quiet}>
        Dismiss
      </button>
    </div>
  );
}
