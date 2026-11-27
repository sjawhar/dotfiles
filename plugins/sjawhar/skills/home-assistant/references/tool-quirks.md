# ha-mcp tool quirks

- Automations: omit `identifier` to create. An update needs `identifier` and `config.id`.
- `ha_set_entity` sets `new_entity_id`, `name`, `enabled`, `area_id`. `name` replaces the whole friendly name, even with `has_entity_name`: pass "Kitchen Outlet Energy", not "Energy", or two devices' sensors look alike.
- `ha_remove_helpers_integrations`: an entry takes `target` = entry_id; a helper takes `target` and `helper_type`. Pass `confirm: true`.
- `ha_manage_backup`: `scope: "snapshot"`. Deleting a snapshot needs a human-set server flag, and it refuses backups with unprovable provenance.
- `ha_manage_hacs` does only `add_repository` and `download`. Uninstall in the UI.
- `ha_get_history` takes an `entity_ids` list and returns states without attributes, about 24h, at most 100 points.
- `ha_manage_energy_prefs` needs `mode` and a fresh `config_hash`: read the prefs with the tool first and pass the `config_hash` that answer returns; read them again after any change, which makes the old hash stale. New `total`/`total_increasing` sensors give `statistics_not_defined` until the recorder's first statistics (about 5 min). That warning is harmless: ignore it.
- Install updates with `ha_call_service` `update.install`, not `ha_manage_updates`.
- `ha_restart`: the transport times out when HA goes down. Poll the base URL for HTTP 200 (about 60-90s).
