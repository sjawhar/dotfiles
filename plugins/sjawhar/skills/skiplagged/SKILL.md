---
name: skiplagged
description: Use when searching flights, hotels, or rental cars; comparing fares across flexible dates; discovering cheap destinations from a fixed origin; or hunting hidden-city ticketing deals. Trigger on multi-city itineraries, fare calendars, "where can I fly cheaply", price-sensitive trip planning, or any time the user wants a sanity-check against Google Flights pricing.
mcp:
  skiplagged:
    type: http
    url: https://mcp.skiplagged.com/mcp
---

# Skiplagged

Live travel search via Skiplagged's public MCP server. No auth required. Invoke via `skill_mcp(mcp_name="skiplagged", tool_name="...", arguments='{}')`.

## Recommended Flow

1. Firm route → `sk_flights_search`; flexible dates → calendar then flight search; flexible destination → `sk_destinations_anywhere`.
2. For multi-city trips, search legs separately and compare the carrier's through-fare. Surface Skiplagged's booking link; its price is indicative until click-time.

## Hidden-City Caveats — Surface These Before Booking

Hidden-city deals are real savings but carry rules a normal traveler doesn't think about. Always flag these to the user before they book a hidden-city itinerary:

- **No checked bags.** Bags get tagged to the final ticketed destination, not the city you're getting off in.
- **One-way only.** If you skip a segment on a round-trip, the rest of the itinerary auto-cancels.
- **Frequent flyer risk.** Airlines have stripped miles and closed accounts for repeat skiplagging on their own program. Don't credit hidden-city flights to that airline's loyalty program.
- **Same name across all bookings.** Mixing passengers across hidden-city tickets has gotten travelers in trouble.
