# OpenAVC v0.30.0

OpenAVC v0.30.0 lets a control on a touch panel page call a function in one of your scripts and pass it values, and lets a control fire an event. A macro that fails partway through now reports which device failed, on the panel that started it. The API key in Settings is generated for you and stored as a hash.

## Touch panel controls and scripts

* A control can call a function in one of your scripts and pass it values. In the UI Builder it is the **Script Function** action, in the Does section of the control's bindings. One function can serve many controls, each passing its own values, and a slider or fader can pass its own position.
* The parameter fields come from the function itself, so the names always match what the script expects. Handlers written with `@on_event` are not offered, because the system calls those itself when their event fires.
* A control can fire an event with the new **Emit Event** action, reaching a trigger, a plugin, or a script handler subscribed to that event. It is the same step the Macros view offers, written straight onto a control.
* The Emit Event macro step fills in `$` values in its payload, the same as every other macro step.

## Panels

* When a macro fails partway through, the panel that started it shows which device failed and why, in the same words a button that talks to a device directly already used.
* A device group command that reached no device at all reports a failed step, instead of the macro finishing with nothing said anywhere.
* Panel pages load with fewer requests, and the files a panel loads to draw itself are no longer counted against the rate limit. A large panel opening on a tablet could come close to it.

## Security

* The API key in **Settings > Security** is stored as a salted hash instead of the key itself. Settings generates one on request and shows it once, because it cannot be read back afterwards. Existing systems convert their key on first start, and an integration already using that key keeps working.
* Settings will not save an API key unless a Programmer password is also set, and will not clear the password while a key is set. A key on its own left the Programmer unreachable from any browser. A system already in that state, set through `OPENAVC_API_KEY` or a hand-edited `system.json`, still starts and gets a warning in the log instead.

## Devices and deployment

* A device connecting over SSH with a blank username or host now says which field to fill in, instead of reporting the device as unreachable.
* Linux: an update recovers when an operating system Python upgrade has left the installed environment unusable, and rolls itself back if it cannot.
* Linux and Raspberry Pi: the firewall helper is now in the release archive and runs when the service starts, so turning HTTPS on in Settings opens its port in ufw or firewalld without a manual firewall edit.
* Updating a system with no internet access is documented for each deployment type, and the Updates view shows that procedure when it cannot reach GitHub. A check that fails no longer reports the system as up to date.
* A busy system no longer shows as offline in the OpenAVC Cloud portal.

## Before you update

* **Script Function changed meaning.** It previously fired an event named `script.call.<function>`, which ran the function only if the script also subscribed to that event by name. It calls the function now. A script written around the old behaviour needs updating.
* Rolling back to a version below v0.30.0 leaves integrations getting 401 responses, because the older version compares the API key literally against what is now a hash. Restore `system.json` from the backup taken just before the update, or set a fresh key and update your integrations to match.
* OpenAVC Cloud support sessions, where you grant OpenAVC staff temporary access to one system from the portal, need that system on v0.27.0 or newer. A system on v0.26.0 or older stops at its own sign-in screen.
* Project files are updated to format 0.12.0 when opened. Nothing in an existing project moves, but a system on an older version cannot run a control that calls a script function or emits an event.
