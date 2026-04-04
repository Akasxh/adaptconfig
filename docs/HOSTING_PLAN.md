# FinSpark Hosting Plan

## Recommended: Render (Free Tier)

### Prerequisites
- GitHub repo pushed to `main` branch
- Gemini API key from Google AI Studio
- Render account (render.com)

### Step 1: Create PostgreSQL Database (5 min)
1. Go to render.com → Dashboard → New → PostgreSQL
2. Name: `finspark-db`
3. Plan: Free (90-day, 256MB)
4. Region: Oregon (closest to Google AI)
5. Copy the **Internal Database URL**

### Step 2: Deploy Backend (10 min)
1. Dashboard → New → Web Service
2. Connect your GitHub repo
3. Settings:
   - **Name**: `finspark-api`
   - **Root Directory**: (leave empty — uses root Dockerfile)
   - **Build Command**: `cd backend && pip install .`
   - **Start Command**: `cd backend && uvicorn finspark.main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Free (750 hrs/month)
4. Environment Variables:
   ```
   APP_ENV=production
   APP_DEBUG=false
   APP_SECRET_KEY=<generate with: openssl rand -hex 32>
   DATABASE_URL=<postgres internal URL from step 1>
   GEMINI_API_KEY=<your key>
   GEMINI_MODEL=gemini-3-flash-preview
   AI_ENABLED=true
   ALLOWED_ORIGINS=["https://finspark-frontend.onrender.com"]
   ```
5. Health Check Path: `/health`

### Step 3: Deploy Frontend (5 min)
1. Dashboard → New → Static Site
2. Connect same GitHub repo
3. Settings:
   - **Name**: `finspark-frontend`
   - **Root Directory**: `frontend`
   - **Build Command**: `npm ci && npm run build`
   - **Publish Directory**: `frontend/dist`
4. Environment Variables:
   ```
   VITE_API_URL=https://finspark-api.onrender.com
   ```
5. Redirect/Rewrite Rules:
   - `/*` → `/index.html` (SPA routing)

### Step 4: Update Frontend Proxy
The frontend needs to call the backend directly (no Vite proxy in production):
- Build with `VITE_API_URL=https://finspark-api.onrender.com`
- The API client uses `import.meta.env.VITE_API_URL || ""` as baseURL

### Step 5: Verify
```bash
# Health
curl https://finspark-api.onrender.com/health

# Frontend
open https://finspark-frontend.onrender.com
```

---

## Alternative: Railway (Faster Deploy)

### Step 1: Install Railway CLI
```bash
npm i -g @railway/cli
railway login
```

### Step 2: Deploy
```bash
cd /path/to/finspark
railway init
railway add --database postgres

# Set env vars
railway variables set APP_ENV=production
railway variables set APP_SECRET_KEY=$(openssl rand -hex 32)
railway variables set GEMINI_API_KEY=<your-key>
railway variables set GEMINI_MODEL=gemini-3-flash-preview

# Deploy
railway up
```

Railway auto-detects the Dockerfile and deploys.

---

## Alternative: Docker Compose on VPS

### Step 1: Provision a VPS
- DigitalOcean Droplet ($6/mo, 1GB RAM)
- Or AWS Lightsail ($3.50/mo)

### Step 2: Clone & Configure
```bash
ssh root@your-vps
git clone https://github.com/Akasxh/finspark.git
cd finspark
cp .env.example .env
# Edit .env with production values
```

### Step 3: Deploy
```bash
docker compose up -d --build
```

### Step 4: Add SSL (Let's Encrypt)
```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d finspark.yourdomain.com
```

---

## Environment Variables Checklist

| Variable | Required | Example |
|----------|----------|---------|
| `APP_ENV` | Yes | `production` |
| `APP_DEBUG` | Yes | `false` |
| `APP_SECRET_KEY` | Yes | `openssl rand -hex 32` |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://user:pass@host/db` |
| `GEMINI_API_KEY` | Yes | Your Google AI key |
| `GEMINI_MODEL` | No | `gemini-3-flash-preview` (default) |
| `AI_ENABLED` | No | `true` (default) |
| `ALLOWED_ORIGINS` | Yes | `["https://your-frontend.com"]` |
| `LOG_LEVEL` | No | `INFO` (default) |

## Post-Deploy Verification
1. `curl https://api.example.com/health` → `{"status": "healthy"}`
2. Open frontend → Dashboard shows 8 adapters
3. Upload a YAML spec → Document parsed
4. Generate config → Field mappings appear
5. Run simulation → 8/8 pass
