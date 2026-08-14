# OpenAVC v0.27.0

OpenAVC v0.27.0 adds custom controls and full custom panel pages, along with a completely rebuilt Matrix control that can configure itself from the device. The cloud also gets major improvements to alerts, webhooks, system health, two-step verification and remote access.

## Custom controls and custom pages

You can now write your own HTML, CSS and JavaScript when the built-in panel elements aren't enough. Custom controls can live alongside normal controls on a Builder page, or a custom page can take over the entire panel screen.

* Custom UI can be written directly in the Programmer or added as files, folders or ZIPs.
* Files live in the project's `ui/` folder and travel with the project through exports, backups and restores.
* Custom controls render directly on the Builder canvas using the project's theme and current system state. They can only control the live system while in Preview or on the actual panel.
* Each control can have its own JSON settings, so the same control can be reused with different configurations.
* **Can reach** controls exactly what a custom control or page is allowed to access, including devices, child entities, variables, macros and page navigation. Nothing is allowed by default.
* Custom pages still work with master elements, overlays, the lock screen, offline notifications, idle behavior and page triggers.

## Matrix, rebuilt

The Matrix control has been substantially reworked. Instead of manually recreating a switcher's inputs, outputs and routing commands, **Set up from a device** can now read that information directly from the driver.

Choose a device and OpenAVC builds the source and destination lists, routing keys and commands for you. Ports can be enabled or removed, renamed and reordered before anything is applied. Devices with multiple independent routing planes can provide a separate Matrix for each one.

There are now three Matrix styles:

* **Tiles**, the new default, shows each destination as a card with its currently routed source.
* **List** gives each destination a source dropdown.
* **Crosspoint Grid** provides the traditional matrix view.

The underlying Matrix model is also much more flexible. Sources and destinations no longer have to be a simple numbered grid, individual destinations can have their own routing keys, unused ports can be omitted, and a Matrix can work with more complex routing devices and AV-over-IP endpoints.

Feedback has also been improved, including clearer handling of routes OpenAVC does not recognize, separate audio/video routing and optional destination locks backed by variables so their state is shared across panels.

Drivers can now explicitly describe their routing when it cannot be reliably inferred. The Driver Builder includes a new Routing section for configuring and validating this information.

## Alert management

The cloud Alerts page has been expanded into a much more useful management view. Alerts can be searched and filtered by client, system, severity and type, and multiple alerts can be acknowledged at once.

Individual alerts now show more detail about the problem, whether it is still active and previous occurrences. Help requests are clearly identified, and acknowledging one reports back to the originating system when it can be reached.

Alert rules are also easier to create and edit, with better state-key selection and validation.

## Webhooks

Alerts can now be delivered to a webhook as JSON.

Webhook routes support optional HMAC-SHA256 signing, test delivery, automatic retries for temporary failures and delivery status directly in the portal. Failed routes are clearly identified so problems can be found without digging through logs.

## System and fleet health

System pages now provide a much better view of what is happening at each space, including CPU, memory, disk usage, uptime, device counts, temperature, connection information and the age of the latest report.

Fleet-level views add version health, searchable and paginated system lists, and direct links from dashboard numbers to the systems behind them. Organization views also surface offline systems and active alerts so problem clients are easier to spot.

Remote session history is now visible to organizations, including who accessed a system, how long the session lasted and whether a system password was required.

## Two-step verification

Two-step verification is now available for portal accounts using an authenticator app.

Accounts can require it for their own users, and integrators can require it for individual client organizations. Users can also enable it for themselves even when it is not required.

Enabling a requirement does not immediately lock out users who have not enrolled yet. They are guided through setup when they next sign in, and administrators can reset two-step verification for a user if needed.

API keys are not affected.

## Remote programming and support access

Organizations can now allow their own signed-in users to remotely open the Programmer without entering the system's local password. This is disabled by default.

For integrator-managed organizations, both the integrator and the organization must allow this access. Disabling it closes the sessions that permission was keeping open.

Remote Access now also distinguishes between Panel and Programmer sessions and gives clearer feedback when a tunnel cannot be opened.

Host network settings remain protected. Changing the OpenAVC host's network configuration from any remote session requires the system's local password.

## The AI assistant through Remote Access

The AI assistant now works properly over a remote connection. Answers stream back as they are written rather than being held until the whole response is finished, so a request that takes a while, or that works through several steps, no longer fails partway with a timeout.

If a request does fail for any reason, your message now stays in the conversation with the reason underneath it, along with buttons to send it again or copy the text. Previously the message was discarded, so a long prompt had to be typed out again from scratch.

## Before you update

* **OpenAVC staff support requires systems to be running v0.27.0 or newer.** Older systems use the previous remote-session protocol and staff support will stop at the system's sign-in screen.
* Existing notification routes configured for the webhook channel need to be recreated with a webhook URL.
* Existing Matrix controls are migrated automatically and keep their sources, destinations and routing information. Old per-panel destination locks are removed unless a variable is assigned to preserve the lock state.
* Cloud API list endpoints are now paginated and return a page plus total count. Anything using an API key and consuming those endpoints may need to be updated.
* Community drivers using the new routing declarations require this version of OpenAVC.
