---
name: skiplagged
description: Use when searching flights, hotels, or rental cars; comparing fares across flexible dates; discovering cheap destinations from a fixed origin; or hunting hidden-city ticketing deals. Trigger on multi-city itineraries, fare calendars, "where can I fly cheaply", price-sensitive trip planning, or any time the user wants a sanity-check against Google Flights pricing — Skiplagged surfaces hidden-city deals other engines deliberately hide.
mcp:
  skiplagged:
    type: http
    url: https://mcp.skiplagged.com/mcp
---

# Skiplagged

Live travel search via Skiplagged's public MCP server. No auth required. Invoke via `skill_mcp(mcp_name="skiplagged", tool_name="...", arguments='{}')`.

Skiplagged's edge over Google Flights and Kayak: it surfaces **hidden-city ("skiplagged") deals** — itineraries where you book a flight with a layover in your real destination and skip the final leg. These are often substantially cheaper than published fares for the same effective route, and other engines deliberately hide them.

## When To Reach For This

- Multi-city or open-jaw itineraries where the user is price-sensitive
- The user has flexibility on dates or destination
- You've already pulled prices from Google Flights and want a sanity check or a better deal
- A leg of a multi-leg trip ends at a major hub — strong hidden-city candidate
- The user explicitly mentions Skiplagged or "hidden city"

## Documented Tools

| Tool | Purpose |
| --- | --- |
| `sk_flights_search` | One-way or round-trip flight search between specific airports/dates |
| `sk_flex_departure_calendar` | Departure-date fare calendar around a target date |
| `sk_flex_return_calendar` | Return-date fare calendar that holds trip length roughly constant |
| `sk_destinations_anywhere` | Cheapest destinations from a given origin in a date window |
| `sk_hotels_search` | Hotels by city/dates/occupancy |
| `sk_hotel_details` | Room-level pricing/amenities for a specific hotel |

Skiplagged also offers rental car search; the tool name isn't in their public docs, so check the tool catalog when you need it.

## Usage Pattern

Parameters use camelCase. Verified shapes below; if a call fails with a `Required` error, the error message names the missing field — use that exactly.

### Search a single flight leg

```
skill_mcp(mcp_name="skiplagged", tool_name="sk_flights_search", arguments='{
  "origin": "SFO",
  "destination": "BOS",
  "departureDate": "2026-05-02",
  "adults": 1,
  "cabinClass": "economy"
}')
```

### Find the cheapest day to fly within a window

```
skill_mcp(mcp_name="skiplagged", tool_name="sk_flex_departure_calendar", arguments='{
  "origin": "IST",
  "destination": "PTY",
  "departureDate": "2026-05-26",
  "adults": 1
}')
```

### Discover cheap destinations from an origin

```
skill_mcp(mcp_name="skiplagged", tool_name="sk_destinations_anywhere", arguments='{
  "origin": "BOS",
  "departureDate": "2026-02-15",
  "adults": 1
}')
```

## Recommended Flow

1. If dates and destination are firm → `sk_flights_search`.
2. If dates are flexible → start with `sk_flex_departure_calendar` to find the cheap day, then `sk_flights_search` on that day for full options.
3. If the destination is flexible → `sk_destinations_anywhere`.
4. For multi-city trips, search each leg separately and compare against a single-airline through-fare on the carrier's own site. Single tickets often win even at a small premium because of bag-through-checking and missed-connection protection.
5. Always surface the booking link Skiplagged returns. Skiplagged's price is indicative; the booking partner's price at click-time is what you'd actually pay.

## Hidden-City Caveats — Surface These Before Booking

Hidden-city deals are real savings but carry rules a normal traveler doesn't think about. Always flag these to the user before they book a hidden-city itinerary:

- **No checked bags.** Bags get tagged to the final ticketed destination, not the city you're getting off in.
- **One-way only.** If you skip a segment on a round-trip, the rest of the itinerary auto-cancels.
- **Frequent flyer risk.** Airlines have stripped miles and closed accounts for repeat skiplagging on their own program. Don't credit hidden-city flights to that airline's loyalty program.
- **Same name across all bookings.** Mixing passengers across hidden-city tickets has gotten travelers in trouble.

## Other Notes

- **Prices are indicative** — always re-verify on the booking link before purchasing.
- **Public server, no auth** — no API key, no account. Heavy automated use may be throttled.
- **Coverage** — Skiplagged covers most major carriers but isn't exhaustive. For high-stakes bookings, cross-check against Google Flights or the airline directly.
