---
name: skiplagged
description: Use when the user wants flights, hotels or rental cars, flexible-date fares, cheap destinations from a fixed origin, or hidden-city ticketing deals. Also use for multi-city itineraries, fare calendars, "where can I fly cheaply", price-sensitive trip planning, or a price check against Google Flights.
mcp:
  skiplagged:
    type: http
    url: https://mcp.skiplagged.com/mcp
---

# Skiplagged

Live travel search through Skiplagged's public MCP server. No auth.

## Call a tool

- With a `skill_mcp` tool: `skill_mcp(mcp_name="skiplagged", tool_name="...", arguments='{}')`.
- Without one, POST JSON-RPC (no session needed). `"method":"tools/list"` gives each tool's input schema.

  ```bash
  curl -sS https://mcp.skiplagged.com/mcp -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"sk_flights_search","arguments":{}}}'
  ```

## Flow

1. Firm route: `sk_flights_search`.
2. Flexible dates: `sk_flex_departure_calendar` (one-way) or `sk_flex_return_calendar` (round trip), then `sk_flights_search`.
3. Flexible destination: `sk_destinations_anywhere`.
4. Multi-city trip: search each leg separately and compare with the carrier's through-fare.
5. Give the user Skiplagged's booking link. Its price is only an estimate until they click it.

## Hidden-city caveats

Before the user books a hidden-city itinerary, tell them all of these:

- No checked bags: bags go to the final ticketed destination.
- One-way only: a skipped segment cancels the rest of the itinerary.
- Do not credit the flight to that airline's loyalty program: airlines remove miles and close accounts for repeat use.
- Use the same passenger name on all bookings: mixed passengers across hidden-city tickets cause trouble.
