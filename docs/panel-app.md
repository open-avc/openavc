# OpenAVC Panel App

> **Status: in development.** The OpenAVC Panel apps for Android and iOS are being built. This guide describes how they'll work when they ship. Install links, store listings, and GitHub Releases will go live once the apps are tested and published. If you reached this page from a QR code, use **Open Panel** to view the web panel in your browser in the meantime.

The OpenAVC Panel app turns an Android or iOS tablet into a dedicated touch panel for an OpenAVC system. It wraps the same web panel you can reach from any browser, but adds the things a browser on a wall-mounted tablet can't do: automatic server discovery, QR pairing, kiosk lockdown, and boot-to-panel auto-start.

## When to Use the App Instead of a Browser

| Scenario | Use the App | Use a Browser |
|---|---|---|
| Wall-mounted tablet in a conference space | Yes | No |
| Employee's personal phone, occasional use | No | Yes |
| Kiosk that must survive end-user tampering | Yes | No |
| Tablet that auto-launches the panel on power-up | Yes | No |
| Quick check of room state from your laptop | No | Yes |

The app is free and open-source (MIT). The web panel inside it is identical to what a browser would show, so if the UI works for you in a browser it will work in the app.

## Install

**Android.** Available from Google Play, or download the signed APK from [GitHub Releases](https://github.com/open-avc/openavc-panel-app/releases) for side-loading.

**iOS.** Available from the App Store.

Both builds require the tablet to be on the same network as the OpenAVC server.

## Pair with Your OpenAVC System

The app has three ways to find a server. Try them in this order.

### 1. Automatic Discovery

Launch the app. Any OpenAVC systems running on the same network appear in the list under **Systems on your network**. Tap one to connect.

This uses mDNS (Bonjour). It works on most home and small-business WiFi networks. Some corporate networks block multicast between WiFi and wired segments. If nothing shows up after 10 seconds, use method 2 or 3.

### 2. QR Code from the Programmer

1. On a computer, open the OpenAVC Programmer IDE (http://your-system-ip:8080/programmer).
2. On the Dashboard, under **Panel Access**, click the **QR Code** button.
3. In the app, tap **Scan QR code** and point the tablet camera at the screen.

The QR contains the panel URL. This is the fastest method when the tablet doesn't have easy keyboard input.

### 3. Manual Entry

Tap **Enter manually** and type the IP address and port of your OpenAVC system (default port is 8080). The app validates the address before saving, so you'll see an error if you typed the wrong address.

Once you've paired successfully, the app remembers the server and reconnects automatically on the next launch.

## Change the Server Later

If you move the tablet to a different space or rebuild the OpenAVC system:

- In a normal install, tap the Back gesture to get the Close / Change server / Keep using this one dialog, then tap **Change server**.
- In a kiosk install, triple-tap the top-left corner of the screen, enter the admin PIN, then tap **Change server** in the admin sheet.

## Kiosk Mode

The app runs in kiosk-suitable full screen from day one. For locked-down unattended use (receptionless conference spaces, lecture halls, worship spaces), you can take it further and lock the tablet completely to the panel so users can't exit to the home screen or open other apps.

Setup differs by platform:

- [Android kiosk guide](panel-app-kiosk-android.md)
- [iOS kiosk guide](panel-app-kiosk-ios.md)

Both guides open with what you get for free before explaining what full kiosk adds, so you can decide how much effort is worth it for your install.

## Troubleshooting

**"Searching the network" never finds anything.**
Your network is probably blocking mDNS between the tablet and the server. Use QR or manual entry instead.

**The panel loads but the Connected badge is red.**
The tablet reached the HTTP server but the WebSocket connection is blocked. Usually a proxy or firewall between the tablet and the OpenAVC system is stripping the WebSocket upgrade headers.

**"Can't reach the system" appears suddenly after working.**
The OpenAVC system became unreachable. Tap **Try again** once it's back up, or **Change server** if you need to point at a different system.

**Panel shows "HTTP 401".**
The OpenAVC system has authentication enabled. Log in from a browser first so the session is established, then relaunch the app.

## Related

- [Getting started with OpenAVC](getting-started.md) — install the server you're pairing to
- [Network and security cut sheet](it-network-guide.md) — ports and firewall rules IT will ask about
