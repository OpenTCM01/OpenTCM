# Deploying OpenTCM

## Configure the origin

Install the runtime dependencies, prepare an authorized classical index, and set a local `.env` using `.env.example`. Set unique values for `OPENTCM_PASSWORD` and `FLASK_SECRET_KEY`, and provide your own API key. Do not publish that file.

For local development:

```bash
python app.py
```

For an always-on installation, use a production WSGI server. Waitress works on Windows and Linux:

```bash
python -m pip install waitress
waitress-serve --listen=127.0.0.1:8000 app:app
```

Use an always-on host with enough disk space for your indexes. The original paragraph KG database can occupy several gigabytes; source data and generated archives take additional space. A GPU is not required when generation uses a remote LLM API.

## Connect a domain with Cloudflare Tunnel

Install `cloudflared` following the [official installation guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/). Create a named tunnel in your own Cloudflare account and route your domain to it. Keep the tunnel credential JSON, account certificate, and token outside the public repository.

An example local configuration is:

```yaml
tunnel: YOUR-TUNNEL-ID
credentials-file: /private/path/to/YOUR-TUNNEL-ID.json
ingress:
  - hostname: your-domain.example
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Run your named tunnel using your private configuration:

```bash
cloudflared tunnel --config /private/path/to/config.yml run
```

For a temporary test URL:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

The origin server and tunnel must both remain running. A laptop that sleeps, shuts down, or loses connectivity stops serving the site. Configure process supervision and restart on your hosting platform for an always-on deployment.

## Conversation data

The password gate provides access to a shared application. All users admitted to one installation share its SQLite conversation store. This release does not provide separate accounts or per-user history isolation.

Restrict a shared installation to a trusted group. Keep database backups private, and do not upload real consultation histories to issue reports, screenshots, or GitHub. For wider public use, first add individual authentication, isolated histories, quotas, and appropriate retention controls.

## Verify an installation

1. Confirm that unauthenticated requests are redirected to login or receive an authentication error.
2. Log in with your configured password and open the welcome and chat pages.
3. Ask a non-sensitive demonstration question; inspect both the answer and a citation popover.
4. Verify language switching, Standard/Deep Reasoning, a follow-up, and history restoration.
5. Repeat the login and page-access checks through the public HTTPS domain.
