# Changelog

## 1.2.0

- Panel entities support color temperature, like the official Nanoleaf light, over the device's own range (1200–6500 K on Shapes). Panels only take RGB, so the temperature is converted to RGB and can look slightly different from the whole device set to the same temperature
- Changing a panel's brightness keeps its color temperature, and panels show the color temperature when the official integration sets one on the whole device. A panel's color temperature survives a restart as long as the device still shows it
- Fixed panel colors never being read back from the device. The integration looked for the running effect under `selectedEffect`, but the Nanoleaf API calls it `select`. After a restart or reload, panel entities showed as on with no color, and the first panel change blanked every other panel. Colors changed outside Home Assistant (e.g. in the Nanoleaf app) now show up at the next poll

## 1.1.4

- Error messages no longer include the Nanoleaf's API token. The API carries the token in its URLs, and since 1.1.3 a connection timeout logged something like `Connection timeout to host http://…:16021/api/v1/<token>/`. The token is now replaced with `**REDACTED**`, and the original exception is no longer chained, so tracebacks don't print it either

## 1.1.3

- Fixed panel entities failing to load ("Failed to fetch Nanoleaf panel layout:") after the Nanoleaf moved to a new IP address. The API client was cached with the old address and reused on every reload until Home Assistant restarted. It now follows the official Nanoleaf integration's entry, including its token, before every request
- If the Nanoleaf can't be reached when panel entities are set up, Home Assistant now retries in the background (30 s, then up to every 3 minutes) instead of giving up until the next manual reload. This also covers a failed first poll, which previously couldn't be retried
- Errors from the Nanoleaf now name the cause, e.g. `Request to Nanoleaf at 192.168.20.28 failed: Unavailable (ClientConnectorError: Cannot connect to host …)`. aionanoleaf2 raises its exceptions without a message, so these previously logged as an empty string

## 1.1.2

- Fixed panel entities turned on in quick succession leaving only one panel lit. Each write replaces the whole display, and concurrent writes merged against the colors from before the burst, so the last one won
- All writes to a device (panel entities and `set_panels`) now go through a single serialized writer. Calls that arrive within 50 ms of each other are merged into one device write
- A panel turned on while the Nanoleaf is off no longer blanks panels lit moments earlier in the same burst. The writer tracks what it lit itself instead of relying only on the parent entity, whose state lags the write
- For 5 seconds after a write, the integration trusts the colors it wrote over the device's read-back, which still reports the previous frame
- Device polls no longer interleave with writes, and keep the write cache in step with the panel entities
- Panel entities now update immediately after a `set_panels` call too
- Turning off a panel right after lighting it from a dark device no longer gets skipped

## 1.1.1

- Isolated all aionanoleaf2 private `_request` calls into a single `NanoleafApiClient` adapter module — breakage from library updates is now confined to one place
- Replaced per-call `ClientSession` creation with the shared Home Assistant HTTP session (`async_get_clientsession`), reducing connection overhead and latency
- The coordinator and service handler now share a single `NanoleafApiClient` instance per device
- Increased panel light entity device polling interval from 30 seconds to 60 seconds

## 1.1.0

- Added `condition` field default of `true` — animation panels without a condition always animate
- Added optional per-panel `light` entities — each physical panel can be exposed as an individual HA light entity with color and brightness control
- Added options flow to enable panel entities and select the target Nanoleaf device per integration entry
- Added support for multiple Nanoleaf devices — the integration can now be added once per device instead of being limited to a single instance
- Panel entities use `ColorMode.HS` for correct brightness slider behavior (avoids cascading scaling errors)
- Panel entities are scoped to the parent Nanoleaf device in the device registry, giving unique entity IDs per device (e.g. `light.nanoleaf_xyz_panel_1`)
- Panel entities immediately reflect the parent Nanoleaf on/off state via state change tracking
- Turning off all panel entities automatically turns off the parent Nanoleaf entity
- When the parent Nanoleaf turns on, panel entities trigger a fresh device poll to pick up current colors

## 1.0.0

- Initial release
- Added nanoleaf_panels.set_panels service
- Added per-panel solid RGB and HSB control
- Added manual multi-frame animation support
- Added built-in animation presets (breathe, pulse, strobe, cycle, fade)
- Added per-panel conditional animation with fallback color
- Added merge behavior to preserve unspecified panel state
