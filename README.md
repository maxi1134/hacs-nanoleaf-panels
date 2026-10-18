# Nanoleaf Panels for Home Assistant

Control individual Nanoleaf panels from Home Assistant — either via a service call in automations or by interacting with each panel as its own HA light entity.

## Features

### Per-panel light entities

When panel entities are enabled (the default), every physical panel is exposed as an individual HA `light` entity with full color, color temperature and brightness control:

- Turn individual panels on or off from the UI, automations, or voice assistants
- Set color, color temperature and brightness per panel independently
- Panel entities reflect the device state automatically:
  - Solid color or color temperature set via the official Nanoleaf integration → all panel entities update to match
  - Turning off all panel entities automatically turns off the parent Nanoleaf
  - Panel entities go off immediately when the parent Nanoleaf is turned off
- Panel entities are scoped to their parent Nanoleaf device in the device registry (unique entity IDs per device, e.g. `light.nanoleaf_xyz_panel_1`)
- Multiple Nanoleaf devices are supported — add one integration entry per device

### `nanoleaf_panels.set_panels` service

For fine-grained automation control, the service lets you set multiple panels in a single call with support for:

- Per-panel solid colors (RGB or HSB)
- Per-panel manual multi-frame animations
- Built-in animation presets: `breathe`, `pulse`, `strobe`, `cycle`, `fade`
- Per-panel conditional animation with fallback colors
- Safe merge behavior — unspecified panels keep their current state

## Requirements

- Home Assistant with the official Nanoleaf integration configured
- A Nanoleaf Shapes, Canvas, or Elements controller supported by the official integration

## Installation (HACS)

[![Open your Home Assistant instance and add this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=UnsignedLong&repository=hacs-nanoleaf-panels&category=integration)

1. Open HACS in Home Assistant.
2. Go to Integrations → 3-dot menu → Custom repositories.
3. Add this repository URL as category Integration.
4. Search for **Nanoleaf Panels** and install.
5. Restart Home Assistant.
6. Go to **Settings → Devices & Services → Add Integration**.
7. Search for **Nanoleaf Panels**, select the Nanoleaf light entity to control, and finish setup.

To expose panel entities for a second Nanoleaf device, add a second integration entry pointing at that device's entity.

### Options

After setup, go to the integration's **Configure** menu to:

- Enable or disable per-panel light entities
- Change which Nanoleaf entity the integration controls

## Per-panel light entities

Panel entities are named `Panel 1`, `Panel 2`, … and linked to the parent Nanoleaf device. They support:

| Capability | Notes |
|---|---|
| On / Off | Turning off all panels also turns off the parent Nanoleaf |
| Color (HS) | Full hue + saturation control |
| Color temperature | Same range as the device (1200–6500 K on Shapes). Panels only take RGB, so the temperature is converted to RGB and can look slightly different from the whole device set to it |
| Brightness | Independent per-panel brightness; changing it keeps the panel's color temperature |
| State sync | Reflects solid color or color temperature set via official integration; tracks parent on/off immediately; reads per-panel colors back from the device |
| Bursts | Panels can be switched together (one call targeting several panels, a `parallel:` block, or back-to-back calls) without delays between them |

## `nanoleaf_panels.set_panels` service

### Service data

| Field | Type | Description |
|---|---|---|
| `entity_id` | string | Target Nanoleaf light entity from the official integration |
| `panels` | list | List of panel updates (see forms below) |

Each panel entry uses `number` (1-based position) or `panel_id` (raw device ID) to identify the panel, then one of the following forms:

**1) Solid color — RGB**
```yaml
- number: 1
  r: 255
  g: 120
  b: 0
```

**2) Solid color — HSB**
```yaml
- number: 1
  hue: 30
  saturation: 100
  brightness: 100
```

**3) Manual frames**
```yaml
- number: 2
  frames:
    - r: 255
      g: 0
      b: 0
      transition_time: 5
    - r: 0
      g: 0
      b: 255
      transition_time: 5
```

**4) Preset animation**
```yaml
- number: 3
  animation: breathe
  color:
    r: 255
    g: 150
    b: 0
  speed: slow
```

Available presets: `breathe`, `pulse`, `strobe`, `cycle`, `fade`. Speed values: `slow`, `medium`, `fast`.

**5) Conditional preset animation with fallback**
```yaml
- number: 4
  animation: pulse
  color:
    r: 255
    g: 80
    b: 0
  speed: medium
  fallback:
    r: 255
    g: 228
    b: 196
  condition: "{{ is_state('binary_sensor.motion', 'on') }}"
```

If `condition` is omitted the animation always runs. If it evaluates to false the `fallback` color is used instead.

## Example automation: mixed status display

Sets all panels at once. Static panels use fixed colors; conditional panels animate when their sensor is active and show warm white otherwise. Unspecified panels keep their current color.

```yaml
alias: Nanoleaf Panel Status
triggers:
  - trigger: state
    entity_id:
      - binary_sensor.motion_sensor
      - binary_sensor.door_sensor
      - sensor.battery_level
      - light.nanoleaf_shapes
conditions:
  - condition: state
    entity_id: light.nanoleaf_shapes
    state: "on"
actions:
  - action: nanoleaf_panels.set_panels
    data:
      entity_id: light.nanoleaf_shapes
      panels:
        # Panel 1: battery level — green when full, red when low
        - number: 1
          r: "{{ [255, (2 * (100 - states('sensor.battery_level') | int)) | round | int] | min }}"
          g: "{{ [200, (2 * states('sensor.battery_level') | int) | round | int] | min }}"
          b: 0
        # Panel 2: warm white static
        - number: 2
          r: 255
          g: 228
          b: 196
        # Panel 3: breathe amber when door sensor active, else warm white
        - number: 3
          animation: breathe
          color:
            r: 255
            g: 150
            b: 0
          speed: slow
          fallback:
            r: 255
            g: 228
            b: 196
          condition: "{{ is_state('binary_sensor.door_sensor', 'on') }}"
        # Panel 4: pulse orange-red when motion active, else warm white
        - number: 4
          animation: pulse
          color:
            r: 255
            g: 80
            b: 0
          speed: medium
          fallback:
            r: 255
            g: 228
            b: 196
          condition: "{{ is_state('binary_sensor.motion_sensor', 'on') }}"
mode: restart
```

## Notes

- `transition_time` is in units of 100 ms.
- Built-in preset animations are rendered as Nanoleaf controller-side custom loops.
- Panel entity state is updated immediately after a write (no round-trip delay).
- The integration polls the device every 60 seconds to sync state changed externally.
- The Nanoleaf only accepts writes for the whole display, so every panel change rewrites all panels. Writes to a device are serialized, and calls arriving within 50 ms of each other are merged into a single write.
- The device address and token come from the official Nanoleaf integration, so when it picks up a new IP address this integration follows. If the Nanoleaf is unreachable when panel entities are set up, Home Assistant keeps retrying in the background and they appear once it responds.

## Repository layout

```text
custom_components/
  nanoleaf_panels/
    __init__.py
    const.py
    light.py
    config_flow.py
    nanoleaf_api.py
    panel_writer.py
    manifest.json
    services.yaml
    strings.json
```

## Development and releases

- Tag releases using semantic versioning, e.g. `v1.1.0`
- Keep `custom_components/nanoleaf_panels/manifest.json` version in sync
- Create GitHub releases from tags so HACS users can install pinned versions

## Disclaimer

This project is community maintained and is not affiliated with Nanoleaf.
