# OpenAVC v0.19.3

- **macOS: the menu-bar Uninstall now works.** In v0.19.2 the "Uninstall
  OpenAVC" menu-bar item quit the menu-bar app but did not remove the
  background service, so the server kept running and came back after a restart.
  It now fully removes OpenAVC: it stops the background service and the menu-bar
  app, removes the application, and keeps your projects and settings unless you
  choose to remove them. The Terminal uninstaller was not affected.
