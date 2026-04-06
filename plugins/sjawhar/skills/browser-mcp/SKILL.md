---
name: browser-mcp
description: Use when browsing websites, clicking elements, filling forms, taking screenshots, reading page content, or automating browser interactions via BrowserMCP
mcp:
  browsermcp:
    command: npx
    args: ["@browsermcp/mcp@latest"]
---

# Browser MCP

Automate browser interactions via BrowserMCP. Use `skill_mcp(mcp_name="browsermcp", ...)` to invoke tools.

Requires the [BrowserMCP Chrome extension](https://chromewebstore.google.com/detail/browsermcp/lhiakgidpmecpkibjnilflhfapjlimej) to be installed and connected.

## Tools

| Tool                     | Purpose                                          |
| ------------------------ | ------------------------------------------------ |
| `browser_navigate`       | Navigate to a URL                                |
| `browser_go_back`        | Go back to the previous page                     |
| `browser_go_forward`     | Go forward to the next page                      |
| `browser_snapshot`       | Capture accessibility snapshot of current page    |
| `browser_click`          | Click an element on the page                     |
| `browser_hover`          | Hover over an element                            |
| `browser_type`           | Type text into an editable element               |
| `browser_select_option`  | Select option(s) in a dropdown                   |
| `browser_drag`           | Drag and drop between two elements               |
| `browser_wait`           | Wait for a specified number of seconds            |
| `browser_press_key`      | Press a keyboard key                             |
| `browser_screenshot`     | Take a PNG screenshot of the current page        |
| `browser_get_console_logs` | Get browser console log output                 |

## Interaction Pattern

Most interaction tools (`browser_click`, `browser_type`, etc.) require an `element` + `ref` pair:

- **`ref`** — opaque element ID from a `browser_snapshot` accessibility tree
- **`element`** — human-readable description of the element (used for permission display)

**Always call `browser_snapshot` first** to get valid `ref` values. Refs are page-session-specific and change on navigation.

Navigation and interaction tools automatically return a fresh snapshot after executing — no need to call `browser_snapshot` separately after them.

## Usage Examples

```
skill_mcp(mcp_name="browsermcp", tool_name="browser_navigate", arguments='{"url": "https://example.com"}')

skill_mcp(mcp_name="browsermcp", tool_name="browser_snapshot")

skill_mcp(mcp_name="browsermcp", tool_name="browser_click", arguments='{"element": "Login button", "ref": "e42"}')

skill_mcp(mcp_name="browsermcp", tool_name="browser_type", arguments='{"element": "Search input", "ref": "e15", "text": "hello world", "submit": true}')

skill_mcp(mcp_name="browsermcp", tool_name="browser_select_option", arguments='{"element": "Country dropdown", "ref": "e88", "values": ["US"]}')

skill_mcp(mcp_name="browsermcp", tool_name="browser_screenshot")

skill_mcp(mcp_name="browsermcp", tool_name="browser_press_key", arguments='{"key": "Escape"}')

skill_mcp(mcp_name="browsermcp", tool_name="browser_wait", arguments='{"time": 2}')
```

## Typical Workflow

1. `browser_navigate` to a URL (returns snapshot)
2. Read the snapshot to find element `ref` values
3. Interact with elements using `browser_click`, `browser_type`, etc.
4. Each interaction returns an updated snapshot — use it for the next step
5. Use `browser_screenshot` when you need a visual capture

## Notes

- The BrowserMCP Chrome extension must be active and connected — without it, all tools error with "No connection to browser extension"
- `browser_screenshot` returns a PNG image (base64), not text
- `browser_get_console_logs` returns JSON-stringified log entries, one per line
- `browser_drag` requires both start and end `element`/`ref` pairs (`startElement`, `startRef`, `endElement`, `endRef`)
- Key names for `browser_press_key` follow web standards: `Enter`, `Tab`, `Escape`, `ArrowLeft`, `ArrowDown`, `a`, `A`, etc.
