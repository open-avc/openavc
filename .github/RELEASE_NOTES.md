# OpenAVC v0.40.0-rc1

A release candidate for 0.40.0, offered only to systems on the Beta channel. Device and plugin logins now stay on the system they were entered on: the project, its backups and exports, and the copies OpenAVC Cloud keeps hold "saved on this system" where each password was. Script code that waits on a slow call no longer holds up the rest of the system.

## Logins

* Device passwords, every setting a driver marks as a login, plugin logins and the Inter-System key are now kept on the system instead of in the project file. The first start after this update moves them there, after backing up the project.
* A saved login shows as **Saved on this system** with **Change** in Edit Device, in a plugin's settings, on Video Streams and on the Inter-System page. Enter a device's login again after changing its driver or duplicating it.
* A device whose login is not saved on this system, such as on a replacement restored from a backup, says **Needs login** and asks for it on its page, with **Use the driver's default** where the driver has one. Restoring a backup, or opening or importing a project, names the devices and plugins that need their login entered. After such a restore, point Present's displays at their new addresses.
* Export can include the logins, sealed with a passphrase you choose, and importing that file asks for it. A project backup restored on the same system brings its logins back.
* The AI Assistant never reads or sets a login. Enter one on the device's page or in the plugin's settings.
* A login written into a stream address is no longer sent to OpenAVC Cloud, and the Inter-System key no longer appears in the log or in reports.
* To go back to an earlier version, use **Rollback** in the Updates view, which puts the logins back first. An older version installed by hand over this one finds no passwords, and its devices with a login cannot sign in.

## Programmer

* A plain `def` script handler, timer or function a control calls now runs on its script's own thread, so a `time.sleep()` or a slow web request holds up that script and nothing else. It still gets 30 seconds, and an `async def` must still never block.
* Fixed: a setting saved right after a plugin starts is kept.

## Panel

* Fixed: a list whose key pattern holds brackets or parentheses now matches them as typed.
