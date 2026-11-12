/**
 * Sound the audio player plugin asks a panel to play.
 *
 * The plugin writes a request into `plugin.audio_player.play_request`: JSON
 * with an `id`, the address to play (`url`, which the plugin resolved and so
 * knows the file's extension, or a `sound` name), a `volume`, or `stop`. Every
 * fresh request carries a new id, so a panel plays each one once, and it
 * remembers the request already in the state it connects to so a reconnect
 * does not replay the last sound. A sound plays at the plugin's master volume
 * times the request's, never while the plugin is muted.
 *
 * Everything stops on a stop request and when the panel's socket drops: a
 * stale chime on reconnect is worse than a missed one. At most eight sounds
 * play at once, and finished ones are let go, so a kiosk up for weeks does not
 * collect audio elements. Browsers refuse sound until somebody touches the
 * page, so a panel plays a silent sound on the first touch; the design canvas,
 * which receives no live state, never does.
 */

import { assetUrl, basePath } from './base_path.js';

const PLAY_REQUEST = 'plugin.audio_player.play_request';
const MUTED = 'plugin.audio_player.muted';
const MASTER_VOLUME = 'plugin.audio_player.master_volume';

const MAX_SOUNDS = 8;
const TOUCHES = ['pointerdown', 'keydown', 'touchstart'];
const SILENCE = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';

/** A request as the plugin wrote it, or null when it is not one. */
function parseRequest(raw) {
    if (!raw) return null;
    let req;
    try {
        req = JSON.parse(raw);
    } catch {
        return null;
    }
    if (!req || typeof req !== 'object') return null;
    return req;
}

/** A level that is not a number counts as full. */
function levelOrFull(n) {
    return isFinite(n) ? n : 1;
}

/** The master volume times the request's, held between silence and full. */
function finalVolume(state, req) {
    const master = Number(state[MASTER_VOLUME] ?? 1.0);
    const reqVol = Number(req.volume ?? 1.0);
    return Math.max(0, Math.min(1, levelOrFull(master) * levelOrFull(reqVol)));
}

/** An address the plugin resolved: a full URL as it is, a server path under the panel's own. */
function absoluteUrl(url) {
    if (!url || typeof url !== 'string') return null;
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
    if (!url.startsWith('/')) return url;
    return basePath() + url;
}

/**
 * The address for a sound the plugin named but did not resolve: a project
 * asset, a URL or server path, or, last of all, the plugin's sounds folder,
 * assuming .mp3 by convention.
 */
function soundUrl(soundId) {
    if (!soundId || typeof soundId !== 'string') return null;
    if (soundId.startsWith('assets://')) return assetUrl(soundId);
    if (['http://', 'https://', '/'].some(prefix => soundId.startsWith(prefix))) return absoluteUrl(soundId);
    return `${basePath()}/api/plugins/audio_player/files/sounds/${encodeURIComponent(soundId)}.mp3`;
}

/** Where a request's sound is: the address the plugin resolved, else one built from its name. */
function requestUrl(req) {
    return req.url ? absoluteUrl(req.url) : soundUrl(req.sound);
}

export class SoundPlayer {
    /** `host`, each read at call time: `state()` and `editMode()`. */
    constructor(host) {
        this._host = host;
        this._allowed = false;
        this._lastRequestId = null;
        this._playing = new Set();
    }

    /** A state key changed; a play request is acted on. */
    noteChange(key, value) {
        if (key === PLAY_REQUEST) this._handle(value);
    }

    /** Remember the request in the state the panel connected to, so it is not replayed. */
    seed() {
        const raw = this._host.state()[PLAY_REQUEST];
        if (!raw) return;
        try {
            const req = JSON.parse(raw);
            if (req && req.id) this._lastRequestId = req.id;
        } catch {
            // Bad JSON means nothing to skip.
        }
    }

    /**
     * Prime playback on the first touch, key or pointer press: a silent sound
     * played from a gesture is what lets later ones play. A refused attempt
     * leaves the listeners for the next gesture to try again.
     */
    allowOnFirstTouch() {
        if (this._host.editMode()) return;
        const allow = () => {
            if (this._allowed) return;
            const silent = new Audio(SILENCE);
            silent.volume = 0;
            silent.play().then(() => {
                this._allowed = true;
                for (const type of TOUCHES) document.removeEventListener(type, allow);
            }).catch(() => {
                // Another gesture will retry; the listeners stay.
            });
        };
        for (const type of TOUCHES) document.addEventListener(type, allow);
    }

    /** Stop every sound playing. */
    stopAll() {
        for (const audio of this._playing) {
            try {
                audio.pause();
                audio.currentTime = 0;
            } catch {
                // The element may already be in a state it cannot be reset from.
            }
        }
        this._playing.clear();
    }

    _handle(raw) {
        const req = parseRequest(raw);
        if (!req || this._heardBefore(req)) return;
        if (req.stop) {
            this.stopAll();
            return;
        }
        const state = this._host.state();
        if (state[MUTED]) return;
        const volume = finalVolume(state, req);
        if (volume <= 0) return;
        const url = requestUrl(req);
        if (url) this._play(url, volume);
    }

    /** Whether this request was the last one heard; a new id becomes the last. */
    _heardBefore(req) {
        if (req.id && req.id === this._lastRequestId) return true;
        if (req.id) this._lastRequestId = req.id;
        return false;
    }

    _play(url, volume) {
        this._makeRoom();
        const audio = new Audio(url);
        audio.volume = volume;
        this._playing.add(audio);
        const letGo = () => this._playing.delete(audio);
        audio.addEventListener('ended', letGo);
        audio.addEventListener('error', () => {
            console.warn(`[panel-audio] failed to load: ${url}`);
            letGo();
        });
        audio.play().catch((err) => {
            // Most often the browser's autoplay rule, before anybody touched
            // the page. The sound is dropped: a late notification is worse
            // than a missed one.
            console.warn(`[panel-audio] play() rejected for ${url}: ${err && err.message}`);
            letGo();
        });
    }

    /** Room for one more sound: finished ones let go, then the oldest stopped. */
    _makeRoom() {
        this._letGoOfFinished();
        this._stopOldestUntilRoom();
    }

    /** Sounds that finished without saying so: looping or streamed ones. */
    _letGoOfFinished() {
        for (const audio of this._playing) {
            if (audio.ended) this._playing.delete(audio);
        }
    }

    _stopOldestUntilRoom() {
        while (this._playing.size >= MAX_SOUNDS) {
            const oldest = this._playing.values().next().value;
            if (!oldest) break;
            try { oldest.pause(); } catch { /* element may be in a bad state */ }
            this._playing.delete(oldest);
        }
    }
}
