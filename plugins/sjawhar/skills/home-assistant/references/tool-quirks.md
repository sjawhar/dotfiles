# ha-mcp tool quirks

- Automations:
  - Omit `identifier` to create one.
  - An update needs `identifier` and `config.id`.
- `ha_set_entity` sets `new_entity_id`, `name`, `enabled`, `area_id`.
  - Pass "Kitchen Outlet Energy", not "Energy", as `name`, or two devices' sensors look alike: `name` replaces the whole friendly name, even with `has_entity_name`.
- `ha_remove_helpers_integrations`: an entry takes `target` = entry_id. A helper takes `target` and `helper_type`.
  - Pass `confirm: true`.
- `ha_manage_backup`: `scope: "snapshot"`. Deleting a snapshot needs a human-set server flag. It refuses backups with unprovable provenance.
- Uninstall in the UI: `ha_manage_hacs` does only `add_repository` and `download`.
- `ha_get_history` takes an `entity_ids` list and returns states without attributes, about 24h, at most 100 points.
- `ha_manage_energy_prefs` needs `mode` and a fresh `config_hash`.
  - Read the prefs with the tool first, and pass the `config_hash` that answer returns.
  - Read them again after any change, which makes the old hash stale.
  - Ignore the `statistics_not_defined` warning that new `total`/`total_increasing` sensors give until the recorder's first statistics (about 5 min): it is harmless.
- Install updates with `ha_call_service` `update.install`, not `ha_manage_updates`.
- `ha_restart`: poll the base URL for HTTP 200 (about 60-90s), because the transport times out when HA goes down.
