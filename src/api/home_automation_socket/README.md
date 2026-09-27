# Home automation socket

Thin **definition + providers** layer for home devices (lights, later cams/thermostats).

This is **not** a marketplace. One folder per vendor; Flask and the Home Automation UI talk to the registry, not to Govee-only imports forever.

```
home_automation_socket/
  types.py              # DeviceInfo, ProviderInfo, HomeAutomationProvider protocol
  registry.py           # list_providers() / get_provider(id)
  providers/
    govee.py            # live — wraps managers.home_automation
    nest.py             # stub — Nest / Google Home SDM later
```

## Adding a provider (e.g. Nest)

1. Add `providers/nest.py` implementing `HomeAutomationProvider`.
2. Register it in `registry.py` `_PROVIDERS`.
3. Implement `available()` from env (e.g. `NEST_CLIENT_ID` / device access project).
4. Wire real list/control when you have credentials — until then keep `available() == False` and a clear `install_hint`.

Do **not** put Nest (or Govee) steps in OOBE. Optional features appear when configured.

## API

- `GET /api/home-automation/providers` — catalog + availability (no secrets).
