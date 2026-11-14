# Despliegue en Hostinger (VPS con Docker Compose)

Cubre CI (tarea 10), despliegue (tarea 11) y reverse proxy (tarea 12). Los dominios y secretos
de este documento son marcadores de posición: nunca se escriben en el repositorio.

## Arquitectura

```
Internet -> [Cloudflare opcional] -> nginx (80/443) -> api:8000
                                         |-> widget (estáticos)
api -> db (pgvector/pgvector:pg16)   api -> redis (redis:7-alpine)
```

Solo `nginx` publica puertos (80 y 443). `api`, `db` y `redis` viven en la red interna de Compose.
Un VPS aloja un directorio por entorno: `/opt/agente/staging` y `/opt/agente/production`.

> Si staging y producción comparten el mismo VPS, no pueden publicar ambos los puertos 80/443.
> Lo recomendado es un VPS por entorno (mismos nombres de secretos, valores distintos).

## 1. Requisitos del VPS

1. Docker Engine y el plugin Compose v2 (`docker compose version`).
2. Usuario de despliegue con acceso a Docker y llave SSH solo para el pipeline.
3. Firewall: permitir solo `22/tcp` (idealmente limitado por IP), `80/tcp` y `443/tcp`.
4. Directorios: `sudo mkdir -p /opt/agente /var/www/certbot && sudo chown -R <usuario>: /opt/agente`.
5. `certbot` instalado en el host.

## 2. DNS

| Registro | Tipo | Destino |
|----------|------|---------|
| `<API_DOMAIN>` | A (y AAAA si aplica) | IP del VPS |
| `<WIDGET_DOMAIN>` | A (y AAAA si aplica) | IP del VPS |

Staging y producción usan subdominios distintos.

## 3. Certificados (Let's Encrypt, webroot)

nginx no arranca sin certificados, así que la primera emisión usa `--standalone` (puerto 80 libre),
y luego se reemite por webroot para que la renovación no requiera detener nginx.

Un certificado por dominio:

```bash
sudo certbot certonly --standalone -d <API_DOMAIN>
sudo certbot certonly --standalone -d <WIDGET_DOMAIN>
```

Después del primer despliegue (nginx ya sirve `/.well-known/acme-challenge/`), cambia el método a webroot:

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d <API_DOMAIN> --force-renewal
sudo certbot certonly --webroot -w /var/www/certbot -d <WIDGET_DOMAIN> --force-renewal
```

Hook de renovación que recarga nginx (`/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/agente/<entorno>
docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
```

```bash
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo certbot renew --dry-run
```

## 4. Cloudflare (opcional)

- DNS en modo proxied (nube naranja) para `<API_DOMAIN>` y `<WIDGET_DOMAIN>`.
- SSL/TLS en **Full (strict)**: el origen ya tiene un certificado válido de Let's Encrypt.
- Network -> **WebSockets: On**.
- Para SSE, evita reglas de caché sobre `/chat/stream` (Cache Level: Bypass).
- El límite de inactividad de Cloudflare en conexiones proxied es de unos 100 s: el cliente debe
  enviar pings/keepalive y reconectar (el widget ya contempla reconexión).

## 5. nginx

Las plantillas están en `deploy/nginx/templates/` y se renderizan al arrancar el contenedor con
`API_DOMAIN` y `WIDGET_DOMAIN`:

- HTTP -> HTTPS con excepción para el desafío ACME.
- TLS 1.2/1.3 y HSTS.
- `/chat/ws`: upgrade de WebSocket, `proxy_read_timeout` y `proxy_send_timeout` de 3600 s.
- `/chat/stream`: SSE con `proxy_buffering off` y `proxy_cache off`.
- Widget: `ai-chat-agent.js` estático, caché corta (300 s) y `Access-Control-Allow-Origin: *`
  para poder embeberlo en cualquier sitio.

**CORS de la API**: no se configura en nginx. Lo resuelve el middleware de FastAPI, que debe
permitir solo los dominios de los clientes que embeben el widget.

## 6. Secretos de GitHub Actions

Crea los Environments `staging` y `production` (Settings -> Environments). En `production` activa
**Required reviewers**. Cada Environment lleva sus propios valores con estos nombres:

| Secreto | Uso |
|---------|-----|
| `HOSTINGER_SSH_HOST` | Host o IP del VPS |
| `HOSTINGER_SSH_USER` | Usuario de despliegue |
| `HOSTINGER_SSH_KEY` | Llave privada SSH del pipeline |
| `HOSTINGER_SSH_KNOWN_HOSTS` | Salida de `ssh-keyscan -t ed25519 <host>` verificada fuera de banda |
| `GHCR_PULL_USER` / `GHCR_PULL_TOKEN` | Usuario y token (solo `read:packages`) para que el VPS descargue la imagen |
| `API_DOMAIN` / `WIDGET_DOMAIN` | Dominios del entorno |
| `POSTGRES_PASSWORD` | Contraseña de PostgreSQL |
| `DATABASE_URL` | Cadena de conexión; el host es `db` (p. ej. `postgresql://agente:<password>@db:5432/agente`) |
| `AUTH_JWT_SECRET` | Secreto JWT (mínimo 32 caracteres) |
| `AUTH_API_KEYS` | JSON `{"cliente": "<sha256>"}` |
| `GEMINI_API_KEY` | Modelo |
| `META_ACCESS_TOKEN` | Meta Graph API |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp Cloud API |
| `X_BEARER_TOKEN` | X API (modo estático) |
| `X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_REFRESH_TOKEN`, `X_USER_ID` | OAuth2 de X con renovación automática (Tool Hub) |
| `META_APP_ID`, `META_APP_SECRET` | Renovación de tokens de Meta y WhatsApp (opcional) |
| `TOKEN_ENCRYPTION_KEY` | Clave Fernet que cifra los tokens guardados en PostgreSQL. Genérala con `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` y no la cambies sin volver a sembrar los tokens |
| `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` | Webhook de WhatsApp (verificación y firma) |
| `CORS_ALLOWED_ORIGINS` | Dominios que embeben el widget, p. ej. `["https://cliente.com"]` (vacío = sin CORS) |

Antes del primer despliegue con tokens persistentes hay que aplicar `migrations/20261113_oauth_tokens.sql`.

Variable opcional (no secreta): `INTEGRATIONS_SANDBOX` (por defecto `true`; ponla en `false` en producción).

Los valores no pueden contener comillas simples ni saltos de línea (el workflow lo rechaza). El
workflow escribe `/opt/agente/<entorno>/.env` con permisos `600` en cada despliegue; nunca se
imprime ni se versiona. `deploy/env.example` lista solo los nombres.

## 7. Flujo de despliegue

- **Staging**: automático cuando el workflow `CI` termina bien en `main`. Construye la imagen,
  la publica en `ghcr.io/<owner>/<repo>:<sha>`, copia `deploy/` y el widget por `scp` y ejecuta
  `deploy/scripts/remote-deploy.sh`.
- **Producción**: manual. Actions -> Deploy -> *Run workflow*, con el SHA completo (ya probado en
  staging) y el ID de la ejecución de CI. Promueve la misma imagen, sin reconstruirla.

El script hace `docker compose pull`, `up -d`, espera a que `api` esté `healthy` y guarda la imagen
anterior en `.deploy/previous_image`.

## 8. Rollback

En el VPS:

```bash
cd /opt/agente/<entorno>
bash scripts/remote-deploy.sh rollback
```

O volver a desplegar una etiqueta concreta: `bash scripts/remote-deploy.sh deploy ghcr.io/<owner>/<repo>:<sha>`.
Si un despliegue falla el health check, el script imprime la imagen anterior como sugerencia.

## 9. Bloquear el merge a `main` si el pipeline falla

Lo ejecuta el administrador del repositorio (no el pipeline). Los checks deben haberse ejecutado
al menos una vez para que GitHub los reconozca.

```bash
gh api -X PUT repos/<owner>/<repo>/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["backend", "widget", "docker"] },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 1 },
  "restrictions": null
}
JSON
```

## Qué no se validó en la entrega

`nginx -t`, `docker compose config` y `actionlint` no se pudieron ejecutar (sin Docker en el entorno
de desarrollo). Valídalos antes del primer despliegue:

```bash
docker compose -f deploy/docker-compose.prod.yml config   # con API_IMAGE y .env definidos
```
