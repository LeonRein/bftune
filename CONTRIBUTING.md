# Contributing

- Run `uv run ruff check . && uv run pytest` before sending changes.
- Any change to the firmware model (`src/bftune/model/`) must cite the Betaflight source file and
  function, and keep the filter-chain gate passing on real logs.
- New firmware versions: regenerate the settings DB with `tools/gen_settings_db.py` and add a
  version switch where the firmware math changed.
- Real logs are welcome as test fixtures only with the owner's permission (logs can contain GPS
  and craft names).
