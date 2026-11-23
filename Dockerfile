# Container image for intranet / any container host (Railway, Fly.io, Cloud Run, on-prem).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PORT=8501

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

EXPOSE 8501
HEALTHCHECK CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8501')+'/_stcore/health')" || exit 1
CMD ["sh", "-c", "streamlit run app/main.py --server.port ${PORT} --server.address 0.0.0.0"]
