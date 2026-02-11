# Deploy notes (prServer + QQbot)

This repo’s `docker-compose.yml` binds services to localhost only:

- prServer: `127.0.0.1:8000`
- QQbot (NoneBot): `127.0.0.1:${HITSZ_QQBOT_HOST_PORT:-8081}` (container listens on `${HITSZ_QQBOT_LISTEN_PORT:-8081}`)

This is intended: public traffic should go through a reverse proxy (Nginx/Caddy) on port 80/443.

## Nginx (Host-based routing)

You **do not** need to “close Nginx” just because you have two websites.
Nginx can serve multiple domains on the same `80/443` by using separate `server { ... }` blocks with different `server_name`.

Templates:

- `deploy/nginx/liulipule.com.conf` → proxies to prServer (`127.0.0.1:8000`)
- `deploy/nginx/jiulipule.com.conf` → placeholder for blog (static or proxy)

Basic steps (typical Linux):

1. Copy the `*.conf` into your Nginx site directory.
2. Validate: `nginx -t`
3. Reload: `systemctl reload nginx`

## NapCat / OneBot V11 reverse WS

If NapCat is on the **same server** as QQbot, point NapCat’s reverse WebSocket to:

- `ws://127.0.0.1:${HITSZ_QQBOT_HOST_PORT:-8081}/onebot/v11/ws`

If port `8081` is already used by another service on that server, change only the **host** port mapping:

- set `HITSZ_QQBOT_HOST_PORT=8082` (or any free port)
- keep `HITSZ_QQBOT_LISTEN_PORT=8081`
- then update NapCat reverse WS target to the new host port

If you need NapCat from another machine, you’ll likely need to expose the QQbot port (or proxy WebSocket via Nginx) — adjust carefully.
