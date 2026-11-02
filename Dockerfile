FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lead_inbox ./lead_inbox
RUN useradd --create-home app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"
# SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, TEAM_API_KEY and ANTHROPIC_API_KEY come from the environment.
CMD ["sh", "-c", "uvicorn lead_inbox.app:build_app --factory --host 0.0.0.0 --port $PORT --proxy-headers"]
