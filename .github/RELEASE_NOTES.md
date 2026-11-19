# OpenAVC v0.41.0-rc1

A release candidate for 0.41.0, offered only to systems on the Beta channel. It includes everything since 0.40.0; the full notes come with 0.41.0.

* A start that fails tries again by itself, then waits for **Start again**. An update or rollback stops the system the way a restart does.
* A save the running system cannot take says why and leaves the space on the project that was running. Changes not yet saved survive a page reload.
* A stream key, password or PIN saved in a macro step or a control's action stays on the system, like a device password.
* On appliance hardware paired to OpenAVC Cloud, the panel software updates from the cloud.
* The project format moves to 0.18.0. Opening an older project renames its own `system.` events to `custom.` and lists each change on the Dashboard.
