# KALA Credit Validation - Vercel + Neon Deployment Guide

## Paso 1: Crear Base de Datos en Neon

1. Ve a [neon.tech](https://neon.tech) y crea una cuenta (o inicia sesión)

2. **Crear nuevo proyecto:**
   - Click "New Project"
   - Name: `kala-credit-validation`
   - Region: `US East (Ohio)` (o la más cercana a tu ubicación)
   - Click "Create Project"

3. **Copiar Connection String:**
   - En el dashboard, verás el connection string
   - Formato: `postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require`
   - **Guárdalo**, lo necesitarás para Vercel

4. **Crear tabla de auditoría:**
   - Click en "SQL Editor" en el menú lateral
   - Copia y pega el contenido de `neon_schema.sql`
   - Click "Run"
   - Deberías ver "CREATE TABLE" y "CREATE INDEX" exitosos

## Paso 2: Preparar Repositorio en GitHub

1. **Crear repositorio:**
   ```bash
   # En tu máquina local
   git init kala-credit-validation
   cd kala-credit-validation
   ```

2. **Copiar archivos del proyecto:**
   ```
   kala-credit-validation/
   ├── api/
   │   └── index.py
   ├── vercel.json
   ├── requirements.txt
   └── README.md
   ```

3. **Crear .gitignore:**
   ```bash
   echo ".env
   __pycache__/
   *.pyc
   .vercel" > .gitignore
   ```

4. **Push a GitHub:**
   ```bash
   git add .
   git commit -m "Initial commit: Credit Validation API"
   git remote add origin https://github.com/tu-usuario/kala-credit-validation.git
   git push -u origin main
   ```

## Paso 3: Deploy en Vercel

1. Ve a [vercel.com](https://vercel.com) e inicia sesión con GitHub

2. **Importar proyecto:**
   - Click "Add New" → "Project"
   - Selecciona el repositorio `kala-credit-validation`
   - Click "Import"

3. **Configurar Environment Variables:**
   
   En la sección "Environment Variables", agrega:

   | Name | Value |
   |------|-------|
   | `KALA_API_BASE` | `https://api.kalaplatform.tech` |
   | `KALA_AUTH_EMAIL` | `sergio.garcia@kala.tech` |
   | `KALA_AUTH_PASSWORD` | `(tu password)` |
   | `DATABASE_URL` | `postgresql://...` (de Neon) |
   | `ANTHROPIC_API_KEY` | `sk-ant-...` |
   | `CLAUDE_MODEL` | `claude-sonnet-4-20250514` |
   | `API_KEY_SECRET` | `(genera una clave segura)` |

   > 💡 Para generar API_KEY_SECRET:
   > ```bash
   > python -c "import secrets; print(secrets.token_urlsafe(32))"
   > ```

4. **Deploy:**
   - Click "Deploy"
   - Espera ~2-3 minutos

5. **Verificar:**
   - Una vez deployado, ve a la URL asignada (ej: `https://kala-credit-validation.vercel.app`)
   - Prueba el health check:
   ```bash
   curl https://tu-proyecto.vercel.app/health
   ```

## Paso 4: Probar la API

### Health Check
```bash
curl https://tu-proyecto.vercel.app/health
```

Respuesta esperada:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "database": "configured",
  "claude_api": "configured"
}
```

### Validar Transacción
```bash
curl -X POST https://tu-proyecto.vercel.app/api/v1/validate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: tu-api-key-secret" \
  -d '{"transaction_id": "0d88e10c-884f-4ea6-a5da-f9a5e72c4459"}'
```

### Consultar Auditoría
```bash
curl https://tu-proyecto.vercel.app/api/v1/audit/0d88e10c-884f-4ea6-a5da-f9a5e72c4459 \
  -H "X-API-Key: tu-api-key-secret"
```

## Paso 5: Verificar en Neon

1. Ve al SQL Editor de Neon
2. Ejecuta:
   ```sql
   SELECT id, transaction_id, decision, status, latency_total_ms, created_at 
   FROM credit_validation_audit 
   ORDER BY created_at DESC 
   LIMIT 10;
   ```

## Troubleshooting

### Error: "Database not configured"
- Verifica que `DATABASE_URL` está configurado en Vercel
- Asegúrate de que incluye `?sslmode=require`

### Error: "Claude API not configured"
- Verifica `ANTHROPIC_API_KEY` en Vercel
- Confirma que la key es válida en console.anthropic.com

### Error 401: "Invalid API Key"
- Usa el mismo valor de `API_KEY_SECRET` en el header `X-API-Key`

### Timeout (>10s)
- Vercel Free tiene límite de 10s
- Upgrade a Pro para 60s, o considera usar Edge Functions

### Cold Starts Lentos
- Primera llamada después de inactividad toma ~3-5s
- Las siguientes son más rápidas

## Monitoreo

### Logs en Vercel
1. Ve a tu proyecto en Vercel
2. Click en "Deployments" → deployment activo
3. Click en "Functions" → "api/index"
4. Ver logs en tiempo real

### Métricas en Neon
```sql
-- Decisiones por día
SELECT DATE(created_at) as fecha, decision, COUNT(*) 
FROM credit_validation_audit 
GROUP BY DATE(created_at), decision 
ORDER BY fecha DESC;

-- Latencia promedio
SELECT 
  AVG(latency_total_ms) as avg_total,
  AVG(latency_kala_api_ms) as avg_kala,
  AVG(latency_claude_ms) as avg_claude
FROM credit_validation_audit 
WHERE status = 'SUCCESS';

-- Errores recientes
SELECT transaction_id, error_message, created_at 
FROM credit_validation_audit 
WHERE status = 'ERROR' 
ORDER BY created_at DESC 
LIMIT 10;
```

## Costos Estimados

| Servicio | Tier | Límite | Costo |
|----------|------|--------|-------|
| Vercel | Hobby | 100GB bandwidth, 10s timeout | $0/mes |
| Vercel | Pro | 1TB bandwidth, 60s timeout | $20/mes |
| Neon | Free | 0.5GB storage, 190h compute | $0/mes |
| Neon | Launch | 10GB storage, unlimited | $19/mes |
| Claude API | Pay-as-you-go | ~$0.003/input, $0.015/output per 1K tokens | Variable |

Para ~1000 validaciones/mes: ~$15-30 en Claude API.
