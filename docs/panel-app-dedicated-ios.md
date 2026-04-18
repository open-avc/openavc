# iOS Dedicated Panel Setup

> **Status: in development.** The OpenAVC Panel app for iOS is being built. This guide describes the setup flow the app will support when it ships. The App Store listing will go live once the app is tested and published. Use this page to plan your install.

This guide covers locking an iPad (or iPhone) to the OpenAVC Panel app. It applies to the iOS build of the OpenAVC Panel app.

> Apple's own developer documentation calls this "Single App Mode" or "Autonomous Single App Mode" (ASAM). Same feature. We call it dedicated panel mode because that's what AV integrators call these tablets.

Apple is much more restrictive than Android about dedicated-panel lockdown. There is no equivalent to Android's Device Owner for individual consumers. Instead, there are three tiers of lockdown, each with real tradeoffs. Pick the tier that matches how determined your users are.

## Before You Start

### Tier 1: Basic Dedicated Panel (No Setup)

Install the app from the App Store, launch it, pair with your OpenAVC system. Done.

**What you get for free:**
- Full-screen panel with no Safari chrome.
- Screen stays on as long as the app is foregrounded.
- App remembers the last paired system and reconnects on relaunch.

**What you don't get:**
- The Home bar at the bottom can swipe the user out to the home screen.
- There's no auto-launch on boot. If the iPad reboots, someone has to tap the app icon.

Basic mode is enough for staff-only spaces or temporary installs.

### Tier 2: Guided Access (Free, Manual Per Session)

Guided Access is built into iOS. It lets you pin the current app to the screen until a PIN is entered. No developer account, MDM, or hardware setup required.

**What you get:**
- Home indicator is disabled.
- App switcher is disabled.
- Control Center and Notification Center are blocked.
- Someone has to enter a PIN to exit back to the home screen.

**Tradeoffs:**
- **Guided Access has to be started manually at the start of each session by triple-clicking the top button on the iPad.** If the iPad reboots or the battery dies, someone has to physically go to the tablet, launch the app, and re-engage Guided Access. There is no way to have iOS auto-engage Guided Access on boot.
- It is a per-device feature, not a per-app policy. Anyone with the PIN can disengage it on a whim.

Good for installs where someone staff-side visits the tablet daily anyway.

### Tier 3: Autonomous Single App Mode (MDM Required)

Autonomous Single App Mode (ASAM) is Apple's real dedicated-panel mechanism. A managed iPad running a specific app can call the system to lock itself in, and survive reboots without staff intervention.

**What you get on top of Tier 2:**
- Lock engages automatically whenever the app launches, including after a reboot.
- Home bar, Control Center, notifications, all Apple-level shortcuts are blocked.
- No PIN required to disengage from within the app because the MDM policy controls it.

**Tradeoffs:**
- **Requires Apple Business Manager enrollment and an MDM provider.** Common ones: Jamf, Mosyle, Apple Configurator. Subscription cost is typically 3 to 6 dollars per device per month.
- iPads need to be enrolled in MDM before they can receive the lock policy. For a brand-new iPad, that's Apple's Device Enrollment Program (DEP).
- For one or two iPads, this is not worth the overhead. For ten plus, it is.

This is what large-scale deployments use.

## Tier 2 Walkthrough: Enable Guided Access

### 1. Turn On Guided Access

1. On the iPad, open **Settings**.
2. Tap **Accessibility**, then **Guided Access**.
3. Toggle **Guided Access** on.
4. Tap **Passcode Settings** and set a passcode. Write it down.
5. Optionally, enable **Accessibility Shortcut** so you can also engage Guided Access by triple-pressing the top button.

### 2. Install and Pair the App

1. Install OpenAVC Panel from the App Store.
2. Launch it and pair with your OpenAVC system (mDNS auto-discovery, QR from the Programmer, or manual IP entry).
3. Confirm the panel loads and everything works.

### 3. Start a Guided Access Session

1. With the OpenAVC Panel app open and the panel visible, triple-click the top button on the iPad.
2. Tap **Guided Access**.
3. Tap **Start** in the upper-right corner.

The iPad is now locked to the panel. The Home indicator is hidden. Notifications are suppressed.

### 4. Exit Guided Access

Triple-click the top button, enter your Guided Access passcode, then tap **End** in the upper-left corner.

### What Happens on Reboot

The iPad returns to its lock screen. Someone has to unlock it, tap the OpenAVC Panel icon, then triple-click the top button and start Guided Access again. There is no way around this at Tier 2. If this is unacceptable, move to Tier 3.

## Tier 3 Walkthrough: Autonomous Single App Mode

This is an overview. Each MDM provider has their own console and wording. Refer to your MDM vendor's docs for exact steps.

### 1. Enroll the iPad in MDM

Either enroll through Apple Business Manager (for iPads bought through a reseller that supports DEP) or manually through Apple Configurator (for iPads bought retail). Manual enrollment requires a USB connection to a Mac; DEP enrollment happens over the air.

### 2. Install the OpenAVC Panel App via MDM

Your MDM console will let you push the App Store listing to the managed iPads. The app arrives already installed when the iPad finishes enrollment.

### 3. Push the Autonomous Single App Mode Policy

Create a restriction profile in your MDM that allows `com.openavc.panel` to use Autonomous Single App Mode. Apply the profile to the iPad or device group.

### 4. Launch the App

When the OpenAVC Panel app launches on a managed iPad with the ASAM profile, it calls `UIAccessibility.requestGuidedAccessSession(enabled: true)` and the system locks immediately. No physical triple-click required. The iPad stays locked across reboots.

### 5. Exit for Maintenance

To unlock, remove the ASAM profile from the iPad via your MDM. The next time the app goes through a lifecycle event, it releases the lock.

## Comparison

| | Basic | Guided Access | Autonomous Single App Mode |
|---|---|---|---|
| Cost | Free | Free | $3 to $6 per device per month |
| Setup complexity | None | Low | High (one-time) |
| Requires hardware access per device | No | Yes, briefly | Yes, one-time enrollment |
| Survives reboot | No | No | Yes |
| Home bar blocked | No | Yes | Yes |
| App switcher blocked | No | Yes | Yes |
| Auto-engages on launch | N/A | No | Yes |

## Or Skip All of This

If the iOS constraints sound like more work than they're worth, Android is a meaningfully easier platform for dedicated panels. An Android tablet plus the OpenAVC Panel app plus a one-time ADB command gets you everything Autonomous Single App Mode gets you, with no ongoing MDM cost. Our Android setup guide walks through it: [Android dedicated panel setup](panel-app-dedicated-android.md).

For customers who want a fully turnkey path on either platform, we also sell pre-provisioned tablets. See [openavc.com/panel-tablet](https://openavc.com/panel-tablet) for availability.

## Troubleshooting

**Guided Access won't start.**
Make sure it's toggled on in Settings > Accessibility > Guided Access. The passcode must be set. Then launch the OpenAVC Panel app first; Guided Access only engages on whatever app is foregrounded when you triple-click.

**iPad keeps disengaging Guided Access.**
Check Settings > Accessibility > Guided Access > Time Limits. Turn off any time limits so the session doesn't auto-end.

**ASAM policy pushed but the app isn't locking.**
The app has to be launched after the policy takes effect. Force-quit and relaunch the OpenAVC Panel app. If the app still doesn't lock, verify in your MDM that the `com.openavc.panel` bundle ID is present in the ASAM whitelist.

## Related

- [OpenAVC Panel App overview](panel-app.md)
- [Android dedicated panel setup](panel-app-dedicated-android.md)
- [Getting started with OpenAVC](getting-started.md)
