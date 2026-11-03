# Integraciones externas (sandbox y secretos)

- Todos los secretos viven en variables de entorno (`.env`, ignorado por git) y se leen como
  `SecretStr`, de modo que no aparecen en `repr()` ni en logs. `.env.example` es la plantilla.
- `INTEGRATIONS_SANDBOX=true` por defecto. Meta y WhatsApp no tienen un host de pruebas
  separado: el sandbox son **apps en modo desarrollo, usuarios de prueba y el número de prueba
  de WhatsApp Cloud**. X no ofrece sandbox: usa un proyecto de desarrollo propio.
- `GET /integrations/status` (requiere JWT) indica qué proveedores tienen credenciales, sin
  devolver ningún valor.
- En producción los secretos deben venir de un gestor (AWS Secrets Manager, GCP Secret Manager,
  Doppler...) inyectado como variables de entorno; el código no cambia.
