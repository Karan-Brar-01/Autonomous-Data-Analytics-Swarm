# Autonomous Data Analytics Swarm

Monorepo for a multi-agent data analytics system: FastAPI + LangGraph backend and Next.js 14 frontend.

## Repository layout

```
.
├── backend/          # FastAPI, LangGraph agents, Docker sandbox tools
│   ├── agents/       # Profiler, Janitor, Statistician, Supervisor
│   ├── tools/        # Code executor + DB helpers
│   ├── core/         # Shared state + graph orchestration
│   ├── main.py
│   ├── config.py
│   └── requirements.txt
└── frontend/         # Next.js 14 (App Router) + Tailwind CSS
```

## Prerequisites

- Python 3.11+
- Node.js 18+ (20+ recommended)
- npm
- Docker Desktop (for later sandboxed code execution)
- A Gemini API key and Supabase Postgres URL when wiring agents/DB

## Backend setup

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then set GEMINI_API_KEY and DATABASE_URL
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API docs: http://localhost:8000/docs  
Health: http://localhost:8000/health

### Backend via Docker

```bash
cd backend
docker build -t adas-backend .
docker run --rm -p 8000:8000 --env-file .env adas-backend
```

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

App: http://localhost:3000

CORS is already configured in `backend/main.py` / `backend/config.py` for `http://localhost:3000` and `http://127.0.0.1:3000`.

## Environment variables

Copy `backend/.env.example` to `backend/.env`. Required for later phases:

| Variable         | Purpose                                      |
|------------------|----------------------------------------------|
| `GEMINI_API_KEY` | Gemini 1.5 Flash via langchain-google-genai  |
| `DATABASE_URL`   | Supabase PostgreSQL (SQLAlchemy URL)         |
