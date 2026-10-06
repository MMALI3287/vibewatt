# Configuration reference

This describes the current behavior in [config.py](../vibewatt/config.py) and
[cli.py](../vibewatt/cli.py). Configuration files are optional JSON objects.

## Precedence, per top-level key

From highest priority to lowest:

1. An applicable command-line override, such as `--tz`.
2. `.vibewatt/vibewatt.json` in the **current working directory**.
3. The file named by the `VIBEWATT_CONFIG` environment variable.
4. The user configuration file (locations below).
5. Built-in defaults.

`load()` starts with defaults and applies files from lowest to highest priority
using shallow dictionary updates. CLI overrides happen afterward in `cli.py`.
Setting `VIBEWATT_CONFIG` does **not** disable the user or project file. Project
configuration is not searched for in parent directories. Relative explicit file
paths are relative to the current working directory; `~` is expanded.

Unreadable/missing files, invalid JSON and JSON values that are not objects are
ignored. A present key with JSON `null` still replaces the lower-priority value;
it does not mean "inherit". The loader does not validate a configuration schema:
unknown keys can be loaded without having any effect in a consumer.

| Platform | User configuration file |
| --- | --- |
| Windows | `%APPDATA%\vibewatt\vibewatt.json`; if `APPDATA` is absent, `~/.vibewatt/vibewatt.json` |
| macOS | `~/Library/Application Support/vibewatt/vibewatt.json` |
| Linux | `$XDG_CONFIG_HOME/vibewatt/vibewatt.json`, or `~/.config/vibewatt/vibewatt.json` when unset/empty |

The compatibility loader also accepts `CCBURN_CONFIG` when `VIBEWATT_CONFIG` is
unset/empty. At the user and project layers it falls back to the old `ccburn`
directory/file only when the corresponding `vibewatt` file does not exist. It
does not merge both names at one layer. Legacy names emit a deprecation warning.

## Worked example: different keys, different layers

Suppose the user file contains:

```json
{"timezone": "UTC", "weeks": 12, "mask_projects": true}
```

Create `example-config.json` in a synthetic project directory:

```json
{"timezone": "Asia/Tokyo", "weeks": 8}
```

Create `.vibewatt/vibewatt.json` in that same directory:

```json
{"weeks": 4}
```

To select the explicit file and override the timezone, use one of these forms:

```sh
# POSIX shell, from the synthetic project directory
VIBEWATT_CONFIG=./example-config.json uv run vibewatt report --tz UTC --offline --no-quota
```

```powershell
# Windows PowerShell, from the synthetic project directory
$env:VIBEWATT_CONFIG = '.\example-config.json'
uv run vibewatt report --tz UTC --offline --no-quota
Remove-Item Env:VIBEWATT_CONFIG
```

Use a disposable shell for these examples. `timezone` is `UTC` from the CLI,
`weeks` is `4` from the project file, and `mask_projects` is `true` from the user
file. Without `--tz UTC`, the timezone is `Asia/Tokyo` from the explicit file.
Without the project `weeks` key, `weeks` is `8` from the explicit file.

These commands illustrate configuration for the normal report: it can sync
local logs. `--offline` disables pricing refresh and `--no-quota` disables quota
lookups; they do not isolate personal data. For a synthetic dashboard, use
`scripts/demo_server.py` as described in [CONTRIBUTING](../CONTRIBUTING.md).

## Nested objects are replaced, not merged

For example, a user file may contain:

```json
{"ai_summary": {"enabled": true, "model": "example-model", "include_project_names": true}}
```

If the project file contains `{"ai_summary": {"enabled": false}}`, the loaded
value is exactly `{"enabled": false}`. It does not retain `model` or
`include_project_names` from the user file or the default object. The weekly
summary consumer supplies its own fallbacks for omitted subkeys (model
`claude-haiku-4-5` and project names excluded). Include the complete desired
object in the higher-priority file. The same replacement rule applies to
`pricing_overrides`, `project_aliases` and `project_paths`.

## Defaults and limits

| JSON key | Built-in default | CLI override / consumer |
| --- | --- | --- |
| `timezone` | `"local"` | `--tz`; report day buckets |
| `day_start_hour` | `0` | `--day-start-hour`; day boundary, 0–23 |
| `weeks` | `53` | `--weeks`; terminal heatmap width |
| `session_length_hours` | `5` | `--session-hours`; rate-limit window |
| `include_sidechains` | `true` | `--no-sidechains` sets false |
| `offline` | `false` | `--offline` sets true; pricing/status network controls |
| `quota` | `true` | `--no-quota` sets false |
| `mask_projects` | `false` | `--mask-projects` sets true |
| `plan_usd_per_month` | `null` | `--plan`; API-equivalent savings comparison |
| `sync_interval_seconds` | `60` | Dashboard background sync interval; `0` disables the periodic loop |
| `pricing_overrides`, `project_aliases`, `project_paths` | `{}` each | Pricing, project labels and concierge project-directory mapping |
| `ai_summary` | `{"enabled": false, "model": "claude-haiku-4-5", "include_project_names": false}` | Opt-in weekly AI summary; API key is environment-only |

`start_of_week` (`"monday"`), `monthly_budget_usd` (`null`) and `heatmap_metric`
(`"cost"`) are present in `DEFAULTS` but currently have no runtime consumers.
Do not rely on changing them to alter output.

Command-specific details:

- Boolean CLI switches are one-way overrides: omitting `--offline` does not
  turn off `offline: true` from a file. There is no inverse CLI switch for each
  boolean.
- `--weeks`, `--session-hours` and `--plan` currently override only for truthy
  values; passing zero does not replace a file value. `--day-start-hour 0`
  does replace a file value because it is checked against `None`.
- `serve` uses argparse's `--host`/`--port` values (defaults `127.0.0.1`/`8777`),
  not JSON `host`/`port`. `--source`, `--days`, `--since` and `--out` are also
  command options, not general JSON overrides.
- Account selection is `--account`, then JSON `account`, then detected identity;
  `serve` passes that selected account to the app.
- `status`/`quota` use their dedicated status path, without the normal report's
  log sync and pricing refresh. `statusline_chain` is a separate optional key
  used by `statusline`; it is not a built-in default or a CLI flag.

## Environment variables are not arbitrary JSON overrides

There is no general `VIBEWATT_<KEY>` mapping for configuration values:
`VIBEWATT_WEEKS`, for example, does not set `weeks`. Provider paths are read
separately from the environment:

| Variable | Purpose |
| --- | --- |
| `CLAUDE_CONFIG_DIR` | Claude configuration/log root |
| `VIBEWATT_COWORK_DIR` | Additional Cowork data location |
| `CODEX_HOME` | Codex data root |
| `VIBEWATT_VSCODE_USER_DIRS` | Copilot Chat VS Code User directories |
| `VIBEWATT_COPILOT_CACHE` | Copilot usage cache location |
| `ANTIGRAVITY_DATA_DIR` | Antigravity conversation data location |
| `VIBEWATT_DATA_DIR` | vibewatt's own store/cache directory |

Putting these names in JSON does not redirect the provider paths. See the
[README source table](../README.md#configuration) for default paths. Keep
credentials such as `ANTHROPIC_API_KEY` out of configuration examples and PRs.
