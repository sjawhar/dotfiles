---
name: skiplagged
description: Use when the user wants flights, hotels or rental cars, flexible-date fares, cheap destinations from a fixed origin, or hidden-city ticketing deals. Also use for multi-city itineraries, fare calendars, "where can I fly cheaply", price-sensitive trip planning, or a price check against Google Flights.
mcp:
  skiplagged:
    type: http
    url: https://mcp.skiplagged.com/mcp
---

# Skiplagged

Run a live travel search through Skiplagged's public MCP server.

## Call a tool

The server needs no auth.

- Call `skill_mcp(mcp_name="skiplagged", tool_name="...", arguments='{}')` when you have a `skill_mcp` tool.
- POST JSON-RPC when you have no `skill_mcp` tool. The POST needs no session. `"method":"tools/list"` gives each tool's input schema.

  ```bash
  curl -sS https://mcp.skiplagged.com/mcp -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"sk_flights_search","arguments":{}}}'
  ```

## Flow

| Search | Do |
| --- | --- |
| Fixed origin, destination and dates | `sk_flights_search` |
| Flexible dates, one-way | `sk_flex_departure_calendar`, then `sk_flights_search` |
| Flexible dates, round trip | `sk_flex_return_calendar`, then `sk_flights_search` |
| Flexible destination | `sk_destinations_anywhere` |
| Multi-city trip | Search each leg, then add the leg prices |
| Hotels | `sk_hotels_search` |
| Rental cars | `sk_cars_search` |
| Award (miles) fares | `sk_award_search` |

- Have the user compare the total of a multi-city trip's leg prices against the carrier's own multi-city fare.
- Give the user Skiplagged's booking link: its price is only an estimate until they click it.

## Hidden-city caveats

Tell the user all of these before they book a hidden-city itinerary:

- No checked bags: bags go to the final ticketed destination.
- One-way only: a skipped segment cancels the rest of the itinerary.
- Do not credit the flight to that airline's loyalty program: airlines remove miles and close accounts for repeat use.
- Use the same passenger name on every booking of the trip.
