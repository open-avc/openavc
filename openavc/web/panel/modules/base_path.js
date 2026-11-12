/**
 * The path this panel's server is reached under, for every address the panel
 * builds for it: '' on the system's own address, `/tunnel/<id>` when the page
 * came through the cloud's Remote Access (`/tunnel/<id>/panel/`).
 *
 * Every request, socket, frame and picture the panel points at its own server
 * starts with it. An address built without it works on the local network and
 * misses through Remote Access, where the cloud forwards only what is under
 * the session's path.
 *
 * Read from the page's address each time it is asked, never once at start-up.
 */
export function basePath() {
    return location.pathname.split('/panel')[0] || '';
}

/**
 * The address of a project asset (`assets://<name>`), or the reference as it
 * is when it names an address already. The name is encoded, since a legal
 * asset name may hold a space or a `#`, and asset names are flat, so
 * encodeURIComponent is safe; the Programmer's getAssetUrl builds the same.
 */
export function assetUrl(ref) {
    if (!ref) return '';
    if (ref.startsWith('assets://')) {
        const name = encodeURIComponent(ref.slice('assets://'.length));
        return `${basePath()}/api/projects/default/assets/${name}`;
    }
    return ref;
}
