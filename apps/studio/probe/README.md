# Proxy Probe

A tiny HTTP service that lives on an overseas VPS (typically US). The main
app in China calls it over HTTPS to check whether a residential proxy is
usable — the probe runs the actual proxied request from a location that can
reach the residential exit nodes.

## Endpoints

`GET /health` — liveness, no auth.

`POST /check` — body `{"proxy": "socks5://user:pass@host:port"}`, header
`X-Probe-Token: <shared secret>`. Returns:

```json
{ "ok": true, "exit_ip": "203.0.113.5", "latency_ms": 842, "error": null }
```

`ok: false` always includes `error`. The probe tries `ipinfo.io` first, then
`ifconfig.co` as a fallback, with a 5 s connect / 8 s read timeout.

## Install (Debian/Ubuntu example)

```bash
sudo useradd -r -s /usr/sbin/nologin probe
sudo mkdir -p /opt/probe && sudo chown probe:probe /opt/probe
sudo -u probe scp server.py requirements.txt /opt/probe/      # or rsync/git
sudo apt install python3-venv -y
sudo -u probe python3 -m venv /opt/probe/.venv
sudo -u probe /opt/probe/.venv/bin/pip install -r /opt/probe/requirements.txt
```

## systemd unit `/etc/systemd/system/probe.service`

```ini
[Unit]
Description=Proxy probe
After=network.target

[Service]
User=probe
WorkingDirectory=/opt/probe
Environment=PROBE_TOKEN=<paste-the-same-token-you-set-in-geelark-studio-.env>
ExecStart=/opt/probe/.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8090 --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now probe
```

## nginx + TLS (sketch)

```nginx
server {
    listen 443 ssl http2;
    server_name probe.example.com;
    # ssl_certificate ... ;
    # ssl_certificate_key ... ;
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

Use Let's Encrypt for the cert. **Do not** expose the probe over plain HTTP
— the shared token would travel in cleartext.

## Configuring the main app

In the geelark-studio `.env`:

```
PROBE_URL=https://probe.example.com/check
PROBE_TOKEN=<the-same-token-as-above>
```

The daily `proxy_check` job (04:00) and any future on-demand checks will
hit this URL.
