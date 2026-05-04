Plugins are now full participants in the rest of the platform. They can declare macro actions and script methods that show up alongside the built-ins. The asset library has been extended past images to handle audio, with native panel playback driven by the new Audio Player plugin. The Pi image and Docker discovery both got real fixes, and Windows updates actually finish on reboot now.

## Plugins

Plugins can register macro actions through a `MACRO_ACTIONS` schema. Their actions appear in the macro builder under a Plugin Actions group, with a form that's generated from the schema. Variable references like `$var.volume` resolve before the handler runs, the same as every other macro step.

Plugins can also register script methods through a `SCRIPT_API` schema. User scripts call them as `openavc.plugins.<plugin_id>.<method>(...)`. The script editor autocompletes the plugin id, lists the methods that plugin exposes, and shows their docs on hover. Sync and async handlers both work.

A plugin's select-field options can now point at a state key instead of being hard-coded. The macro builder reads the live list from there, so a plugin like Audio Player can offer a "pick a sound" dropdown that updates when you upload a new sound, without the user typing filenames.

The plugin detail page in the IDE now renders an optional `usage` field as Markdown, so plugins can show how to actually use them (from a macro, from a script, from a button) without a user having to dig through a README.

Enabling or disabling a plugin now refreshes the macro builder's action list immediately, instead of after a page reload.

## Audio in the asset library

Asset uploads accept mp3, wav, ogg, and m4a in addition to images. Per-file caps are 50 MB for images and 200 MB for audio, with a per-project total of 5 GB.

The asset browser has filter chips that switch between all assets, images only, and audio only. Audio entries render with a native player so you can preview a sound before assigning it.

The Program tab now has an Assets section that lists every uploaded asset, so you can manage them in one place instead of going through an image property field on a UI element.

Panels can play audio directly through the panel runtime, driven by the Audio Player plugin. There's no iframe and no UI element to add. Every connected panel plays in sync.

## Pi image

The image build was finishing in a state where first boot dropped you on a blank labwc desktop instead of the OpenAVC panel, because Pi OS's first-boot rename wizard was overriding our auto-login user. The image now boots straight into the configured user with the panel running. The build verifies this before producing an image, so a future regression of this class fails the build instead of shipping a broken `.img`.

## Docker

Discovery scans were failing inside Docker because containers don't get raw socket access by default. The shipped Compose file now uses host networking with `NET_RAW`, and the image grants `cap_net_raw` to ping. The install path is also simpler now: download the Compose file, run `docker compose up -d`.

## Windows updater

The post-install task that completes a Windows update on next boot was being scheduled but never firing. `schtasks` was silently truncating the start time into the past, so Task Scheduler skipped the trigger. Updates now register the task via XML, and the trigger sticks.
