---
name: forward-port
description: Use when Sami needs to see, in his laptop browser, a web app or any TCP service running on the devbox or inside an agent box - a Vite/Next/Storybook dev server, a local API or UI preview, a demo stack, "can I see it", "send me a link", "open localhost:5173". `forward port <ports>...` makes his laptop's localhost:<port> reach this machine's loopback on the same port (inside an agent box, the box's own loopback, which nothing else can reach) for as long as the command runs. Also use before a login inside a box whose redirect targets a localhost callback port known in advance. Triggers - forward port, show Sami the app, preview a local UI, laptop localhost, port forward, ssh -L, dev server in a box, box loopback, localhost link for Sami.
---

# Showing Sami a local port

`forward port <port>...` makes the laptop's `localhost:<port>` reach `127.0.0.1:<port>` (or `[::1]:<port>`) on the machine where the command runs, until the command stops. Inside an agent box that is the box's own loopback. Port numbers stay the same and the bytes are untouched, so a `localhost` origin, a `__Host-` cookie, or the app's own self-signed localhost certificate works unchanged in his browser. Nothing else reaches a box's loopback: `ssh -L`, published docker ports and binding the app to the box's network address are all dead ends.

## Do it

1. Start the app listening on loopback (`127.0.0.1`, `::1`, `localhost`, or all interfaces).
2. Hold every port the browser will touch - the app, plus any login, fixture or API server it redirects to or calls from the page - in one supervised process beside the app:

   ```json
   {"op": "start", "name": "fwd-app", "application": "forward", "args": ["port", "5173", "41575"], "restart": "on-failure", "ready": {"log": "holding until stopped", "timeout": 30}}
   ```

   It prints `forward: laptop localhost:5173 -> 127.0.0.1:5173 here` for each port, then `forward: holding until stopped`. Keep `restart: on-failure`: a restart of the host's `forward serve` ends every hold, and `forward port` exits 1 so its supervisor brings it back.
3. Send Sami the URL the app serves, e.g. `https://localhost:5173/`. Not a `forward url` link: that is the file preview.
4. Stop the process when he is done. Nothing lingers: the devbox side ends with the process, and the laptop frees the port within three minutes.

A port you did not hold is refused, never routed to whatever else uses that number. Two sessions cannot hold one port; the second is refused.

## When it fails

| Output | Meaning | Do |
|---|---|---|
| `no local bridge at /run/user/1000/forward/arm.sock` | The host's `forward serve` is down, or the box does not mount `/run/user/1000/forward` | On the host: `systemctl --user status forward-serve`. In a box: report it; a new box gets the mount |
| `the bridge refused port N: it is privileged, reserved by forward, or unsafe to expose` | Ports below 1024, forward's own 128xx ports, and 2345, 2375, 2376, 3306, 5432, 5678, 6379, 8001, 9229 are never forwarded | Serve the app on another port |
| `port N is already held by another process on this devbox` | Another session holds it | Another port |
| `the laptop refused port N: ...` | Something on Sami's laptop already uses that port | Another port |
| `the laptop daemon at ... did not answer a port hold: it predates forward port` | Sami's laptop runs a `forward` older than 3.4.0 | Tell him that line; the upgrade is his |
| `the local bridge at ... did not answer a hold; it predates forward port` | The host's `forward serve` is older than 3.4.0 | Upgrade forward on the devbox, restart `forward-serve` |
| `the bridge ended the hold on port N` | The host's `forward serve` restarted | Nothing, if it runs under `restart: on-failure` |
| `a laptop connection for port N found nothing listening on 127.0.0.1:N or [::1]:N here` (the browser gets connection refused) | The app is not listening on loopback | Fix the app's bind address; the hold stays |
| `...; the devbox side stays held, retrying every minute` | The laptop is asleep or off the tailnet | Nothing; renewal resumes when it is back |

## Logins inside a box

A CLI login inside a box that sends Sami's browser to `http://localhost:<port>/...` for its callback fails by default: `forward open` arms that port on the host, and the host's loopback is not where the box's callback server listens. When the callback port is known before the login starts, hold it first (`forward port <port>`), then run the login; the redirect then reaches the box. A login that picks a random port at run time cannot be fixed this way.

## Not this skill

- A file on the devbox: `forward url <path>`.
- Driving Sami's own Chrome from the devbox: `forward-browser`.
