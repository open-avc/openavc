# Android Dedicated Panel Setup

> **Status: in development.** The OpenAVC Panel app for Android is being built. This guide describes the setup flow the app will support when it ships. Links to the signed APK and Google Play listing will go live once the app is published. Use this page to plan your install, but don't try to follow the ADB or QR provisioning steps yet.

This guide walks through locking an Android tablet to the OpenAVC Panel app so end users can't exit to the home screen, open other apps, or pull down the notification shade. It applies to the Android build of the OpenAVC Panel app.

> Android's own developer documentation calls this "kiosk mode" or "Lock Task Mode." Same feature. We call it dedicated panel mode because that's what AV integrators call these tablets. If you're searching the Android docs or Stack Overflow and see "kiosk," that's the same thing.

## Before You Start

Decide how locked-down the install needs to be. There are two tiers.

### Tier 1: Basic Dedicated Panel (No Setup)

Install the app, launch it, pair with your OpenAVC system. Done.

**What you get for free:**
- Full-screen panel with no visible browser chrome, URL bar, or Android status bar.
- Back gesture shows a confirmation dialog instead of exiting.
- Screen stays on as long as the app is foregrounded.
- App remembers the last paired system and reconnects on relaunch.

**What you don't get:**
- A determined user can still swipe down from the top of the screen to reveal system controls and exit to the home screen.
- If someone power-cycles the tablet, the app won't auto-launch until they tap its icon.

Basic mode is enough for staff-only spaces, huddle spaces, and temporary installs.

### Tier 2: Full Dedicated Panel (Device Owner)

Provision the app as the Android "Device Owner" and the tablet becomes fully inescapable. This is what you want for public-facing installs, classrooms, worship spaces, and any install you don't want people tampering with.

**What full dedicated-panel mode adds on top of basic:**
- Home and recent-apps buttons are disabled.
- The notification shade and quick-settings pull-down are blocked.
- The tablet auto-launches the panel on boot.
- Triple-tap the top-left corner plus an admin PIN is the only way to exit.

**Tradeoffs:**
- You can only provision a tablet as Device Owner on a factory-fresh device with no Google account signed in.
- Provisioning requires either a USB computer (ADB) or a one-time enterprise QR scan. See the two methods below.
- Setting up Device Owner is irreversible without a factory reset, so commit to it before you mount the tablet on a wall.

## Method A: ADB (Best for One or Two Tablets)

Time: about 10 minutes per tablet, assuming you already have ADB installed.

### 1. Factory Reset the Tablet

Skip this step only if the tablet has never been set up. Otherwise: Settings > System > Reset > Erase all data.

### 2. Complete the Welcome Screens Without Signing In

When the tablet restarts after the reset, walk through setup but **do not sign in with a Google account**. Skip every prompt that asks. Google account presence blocks Device Owner provisioning.

### 3. Enable Developer Options and USB Debugging

1. Settings > About tablet > tap **Build number** 7 times.
2. Settings > System > Developer options > enable **USB debugging**.
3. Connect the tablet to your computer over USB. Accept the RSA fingerprint prompt on the tablet.

### 4. Install the OpenAVC Panel App

Download the signed APK from [GitHub Releases](https://github.com/open-avc/openavc-panel-app/releases) and push it:

```bash
adb install OpenAVCPanel-release.apk
```

Or use the Google Play build. Either works.

### 5. Set the App as Device Owner

From your computer, with the tablet still connected over USB:

```bash
adb shell dpm set-device-owner com.openavc.panel/.kiosk.AdminReceiver
```

Success looks like:

```
Success: Device owner set to package ComponentInfo{com.openavc.panel/com.openavc.panel.kiosk.AdminReceiver}
```

(The internal class name still says "kiosk" because Android's SDK calls the feature that. The user-facing app calls it dedicated panel mode.)

If you see `not allowed to set device owner: there are already some accounts`, sign out of every Google/Samsung/etc. account on the tablet and try again.

### 6. Launch the App and Lock the Panel

1. Open the app and pair with your OpenAVC system as usual.
2. Triple-tap the top-left corner of the screen.
3. In the admin sheet, tap **Panel settings**.
4. Set an admin PIN. Write it down somewhere you won't lose it.
5. Flip the **Lock the panel** switch to on.
6. Tap the back arrow in the toolbar.

The panel locks immediately when you return to it. Home and recents are disabled. Reboot the tablet to confirm the app auto-launches back into the panel.

## Method B: QR Enterprise Provisioning (Best for Fleets)

If you're deploying more than a handful of tablets, QR provisioning is faster because there's no USB cable involved. Each tablet just needs to scan one QR code during its initial setup.

Time: about 3 minutes per tablet.

### Generate the Provisioning QR

Use a tool like [Andy's Android Enterprise QR Generator](https://www.androidenterprisepartners.com/tools/qr-generator/) and fill in:

- **Admin package name:** `com.openavc.panel`
- **Admin component:** `com.openavc.panel.kiosk.AdminReceiver`
- **Download URL:** the signed APK URL from our GitHub Releases
- **SHA-256 checksum:** the checksum published alongside the APK on the release page

Save or print the resulting QR.

### Provision a Tablet

1. Factory reset the tablet.
2. On the first welcome screen (the one that says "Welcome" or "Hi there"), tap the same spot 6 times in a row. The tablet will prompt you to connect WiFi and then open a QR scanner.
3. Scan the provisioning QR. The tablet downloads the APK, installs it, enables the app as Device Owner, and finishes setup unattended.

From that point the steps inside the app match Method A steps 6 and on.

## Basic vs. Full Dedicated Panel at a Glance

| | Basic | Full |
|---|---|---|
| Setup time | 0 | 10+ minutes |
| Requires factory reset | No | Yes |
| Blocks home button | No | Yes |
| Blocks recents | No | Yes |
| Blocks notification shade | No | Yes |
| Auto-launches on boot | No | Yes |
| Exit method | Back gesture + dialog | Triple-tap + PIN |

## Exit Full Dedicated Panel Mode

The only way out of full lockdown is:

1. Triple-tap the top-left corner of the screen within 2 seconds.
2. Enter the admin PIN.
3. In the admin sheet, tap **Panel settings**.
4. Flip **Lock the panel** to off.

If you forget the PIN, a factory reset is the only recovery path. The tablet will lose its Device Owner status and you'll have to provision it again.

## Uninstalling or Reassigning a Tablet

Once a tablet is Device Owner, you can't simply uninstall the OpenAVC Panel app. You have two options:

- **Keep the tablet locked to a different OpenAVC system:** use the in-app Change server flow.
- **Free the tablet for other uses:** from a connected computer, run `adb shell dpm remove-active-admin com.openavc.panel/.kiosk.AdminReceiver`. This relinquishes Device Owner status. The tablet is then free to be uninstalled or factory reset normally.

## Or Skip All of This

If you don't want to touch ADB or QR provisioning, we sell pre-provisioned Android tablets that arrive ready to scan a QR and pair with your OpenAVC system. Cost is around the price of the tablet itself plus a small provisioning fee. See [openavc.com/panel-tablet](https://openavc.com/panel-tablet) for availability and SKUs.

## Troubleshooting

**"not allowed to set device owner: there are already some accounts".**
Sign out of every account in Settings > Accounts. If the tablet came from a previous owner, factory reset it before trying again.

**The app was provisioned but the status says "Basic".**
Check Settings > Security > Device admin apps. OpenAVC Panel should be listed as an enabled device admin. If it isn't, provisioning didn't complete; run the ADB command again.

**The tablet reboots and lands on the launcher instead of the panel.**
Confirm the **Lock the panel** toggle is on in Panel settings. If it's on and the panel still doesn't auto-launch, the manufacturer may have stripped the BOOT_COMPLETED broadcast; a few budget tablet OEMs do this. Try a different brand or escalate through [community support](https://github.com/open-avc/openavc-panel-app/issues).

**Triple-tap doesn't register.**
Tap the top-left 80dp (about a postage stamp) of the screen. All three taps need to land within 2 seconds. If the panel has a visible element in that corner, the taps still count even if the element reacts to them.

## Related

- [OpenAVC Panel App overview](panel-app.md)
- [iOS dedicated panel setup](panel-app-dedicated-ios.md)
- [Getting started with OpenAVC](getting-started.md)
