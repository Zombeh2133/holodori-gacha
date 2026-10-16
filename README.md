Holodori Gacha
Holodori Gacha is a gacha-history tracker for hololive Dreams. It imports a player's pull history, stores it in PostgreSQL, and presents account, banner, rarity, pity/spark, and pull-history data through a web dashboard.
The project is split into a FastAPI backend, a Next.js frontend, a PostgreSQL database, a local game importer, and an artwork cache backed by `holodori-asset-tools`.
Features
Discord OAuth sign-in
Multiple game accounts per Discord user
US, ASIA, and JP account support
Banner-specific and lifetime pull statistics
3★, 4★, and 5★ pull tracking
Pull history with rarity and banner filtering
Pity / spark progress
Recent 5★ pull display
Per-banner pulled-card summaries
Duplicate-safe imports
Local PowerShell importer for the installed game client
Card and banner artwork retrieval and caching
Background artwork warmup after imports
Responsive desktop and mobile UI
Restricted site-wide statistics view
Project Structure
```text
holodori-gacha/
├── api/                  # FastAPI application
│   ├── app/
│   │   ├── api/          # API routes
│   │   ├── assets.py     # Artwork resolution/cache service
│   │   ├── importing.py  # Import normalization/storage
│   │   ├── models.py     # SQLAlchemy models
│   │   └── main.py       # FastAPI entry point
│   ├── Dockerfile
│   └── requirements.txt
│
├── web/                  # Next.js / React frontend
│   ├── app/
│   ├── public/
│   ├── Dockerfile
│   └── package.json
│
├── local_importer/
│   └── import_holodori.py
│
├── scripts/              # PowerShell setup/run/import helpers
├── docker-compose.yml    # Local PostgreSQL
├── holodori-mascot.png
└── README.md
```
Tech Stack
Backend
Python
FastAPI
SQLAlchemy Async
PostgreSQL
asyncpg
HTTPX
Pillow
PyCryptodome
Frontend
Next.js 15
React 19
TypeScript
Assets
Artwork is resolved through:
```text
holodori-asset-tools
```
The API keeps a local asset catalog and image cache so card and banner artwork does not need to be re-downloaded for every request.
How Imports Work
The normal import flow is:
```text
hololive Dreams
      ↓
Local importer
      ↓
Holodori Gacha API
      ↓
PostgreSQL
      ↓
Dashboard / History
```
The importer reads the newest local `DC*` credential file from the installed game only when an import is run.
It then retrieves the player's current gacha history, normalizes the results, and sends the pull data to the API.
Each stored pull includes data such as:
Banner
Card ID
Card name
Rarity
Pull time
Pull order
Occurrence index
Featured status when available
Imports are duplicate-safe. Pulls that are already stored are ignored rather than inserted again.
Automatic Artwork
After an import completes, the API schedules an artwork warmup.
The warmup scans stored:
Card IDs
Banner IDs
and attempts to resolve their artwork through the current asset catalog.
Card artwork is cached locally after it is first resolved.
Banner artwork also supports additional hints such as banner name, type, and first pull date to improve matching for historical banners.
The in-memory asset catalog refresh window is currently approximately 30 minutes.
Card Metadata
The local importer can refresh card metadata from Master data including:
```text
Card.json
Character.json
LangCard_Eng.json
```
This is used to resolve card names, rarities, character IDs, and character metadata during imports.
The importer's Master-data cache is refreshed periodically rather than downloaded for every import.
Discord Authentication
The API uses Discord OAuth with the `identify` scope.
Browser login state is stored in a signed session cookie.
The application configuration explicitly keeps Discord OAuth tokens out of the browser session cookie and does not persist them as part of that session state.
Local Development
Requirements
Install:
Git
Python
Node.js 20+
npm
Docker Desktop
The production API Docker image currently uses Python 3.12 and the web Docker image uses Node 22.
1. Clone the Repository
```powershell
git clone https://github.com/Zombeh2133/holodori-gacha.git
cd holodori-gacha
```
2. Create `.env`
The API reads configuration from a root `.env` file.
Example:
```env
POSTGRES_DB=holodori_gacha
POSTGRES_USER=holodori
POSTGRES_PASSWORD=change-me-local-only
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

API_HOST=127.0.0.1
API_PORT=8000
API_RELOAD=true

SESSION_SECRET=replace-this-with-a-long-random-secret
SESSION_HTTPS_ONLY=false

DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_REDIRECT_URI=http://localhost:8000/auth/discord/callback

FRONTEND_URL=http://127.0.0.1:3000

GII_MURIN_INTEGRATION_KEY=local-dev-change-me

ASSET_CACHE_DIR=data/asset-cache
ASSET_CATALOG_PATH=data/asset-cache/octo_list.json
```
> The setup scripts reference example environment files. If those example files are not present in your checkout, create the environment files manually as shown above.
3. Set Up the API
From the repository root:
```powershell
.\scripts\setup-local.ps1
```
This will:
Start the local PostgreSQL container
Create a Python virtual environment
Install API dependencies
Install local asset tooling when available
Then start the API:
```powershell
.\scripts\run-api.ps1
```
The API runs at:
```text
http://127.0.0.1:8000
```
Swagger:
```text
http://127.0.0.1:8000/docs
```
4. Set Up the Web App
Install the frontend dependencies:
```powershell
.\scripts\setup-web.ps1
```
If `web/.env.local.example` is not present, create:
```text
web/.env.local
```
with:
```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```
Then run:
```powershell
.\scripts\run-web.ps1
```
The website runs at:
```text
http://127.0.0.1:3000
```
Local Game Importer
The importer is located at:
```text
local_importer/import_holodori.py
```
The PowerShell wrapper is:
```text
scripts/import-holodori.ps1
```
For the full local importer configuration, run:
```powershell
.\scripts\setup-local-importer.ps1
```
The importer configuration expects the game API values used by the client integration, while the game credential itself is read from the newest local `DC*` file at runtime and should not be placed in the importer environment file.
Example import:
```powershell
.\scripts\import-holodori.ps1 `
    -ImportToken "YOUR_IMPORT_TOKEN" `
    -ApiBase "http://127.0.0.1:8000"
```
A region can also be supplied:
```powershell
.\scripts\import-holodori.ps1 `
    -ImportToken "YOUR_IMPORT_TOKEN" `
    -ApiBase "http://127.0.0.1:8000" `
    -Region US
```
Supported explicit region values are:
```text
US
ASIA
JP
```
Database
Local PostgreSQL is defined in:
```text
docker-compose.yml
```
Start it manually with:
```powershell
docker compose up -d postgres
```
Stop it with:
```powershell
docker compose down
```
The database volume is persistent unless the Docker volume is explicitly removed.
API
The backend is exposed through FastAPI.
Major route groups include:
```text
/auth
/api/me
/api/accounts
/api/import
/api/assets
/api/admin
/api/integrations
/api/health
```
Exact routes can be viewed through Swagger at:
```text
http://127.0.0.1:8000/docs
```
Building the Web App
From the `web` directory:
```powershell
npm run build
```
Run the production build with:
```powershell
npm run start
```
The web Dockerfile also performs a production `next build`.
Docker
The repository contains separate production Dockerfiles for the API and web services.
API
```text
api/Dockerfile
```
Web
```text
web/Dockerfile
```
The frontend build expects:
```text
NEXT_PUBLIC_API_BASE_URL
```
as a build argument/environment value.
Artwork Cache
By default, API artwork data is stored under:
```text
data/asset-cache/
```
The catalog is stored at:
```text
data/asset-cache/octo_list.json
```
Cached card and banner images are generated as PNG files after successful resolution.
Useful PowerShell Scripts

Development Notes
New banners
New imported banner records are stored automatically. The asset system attempts to resolve their artwork from the current Holodori asset catalog.
New cards
New card IDs are stored from imported pull history and their artwork is resolved automatically when the current asset catalog contains the matching asset.
New character names
The importer reads character metadata from Master data. The frontend also currently contains a static `characterMap.ts` used for character-name prefixes in some UI locations, so a completely new character may require that frontend mapping to be updated until that remaining lookup is moved fully to Master-driven metadata.
Privacy Notes
The application stores the data required to provide account and pull-history tracking, including linked Discord identity information, game-account records, imported pull history, and import records.
Artwork and shared banner metadata are not user-specific data.

Repository
```text
Zombeh2133/holodori-gacha
```
