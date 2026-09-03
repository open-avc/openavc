# OpenAVC v0.32.1

Releases are now signed, so a system checks that an update is genuine before it installs it. An update that installs correctly can no longer be undone by a restart.

## Updates

* **An update stays installed.** A system restarted within a minute of an update could come back on the version it was on before, with the Updates page still reporting the update a success. Appliance hardware was affected every time, because it is shut down by its supervisor rather than closing down gracefully. An update is now confirmed by the new version starting up, so restarting or power cycling straight after one cannot undo it.
* **A system that genuinely cannot run a new version still goes back.** That check now counts failures within a single power-on, so a panel switched off and on by the person who just installed the update is not mistaken for a version that will not start.
* **Update History says what you are actually running.** After a rollback the newest entry reads "reverted" instead of continuing to claim the update succeeded.

## Release signing

* **The files the update system installs are signed.** Each one has a signature listed next to it on the release page, and systems on Linux, Raspberry Pi and appliance hardware, and macOS check it against a trusted key before installing anything. An update that has been altered in transit, or on the release page itself, is refused rather than applied. The Raspberry Pi disk image is the exception: it is checked against its published checksum, as before, because it is written to a card rather than installed by the system.
* **Signature checking begins once a system is running v0.32.1**, which is the release that brings the trusted key with it. The update onto v0.32.1 itself is checked against its published checksum, the way earlier updates were.
* **Installing by hand on a system with no internet:** take the `.sig` file listed beside the file you download and copy the two together. The offline instructions in the documentation cover this.
