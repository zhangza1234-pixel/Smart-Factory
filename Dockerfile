FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt 'PyMySQL[rsa]==1.1.1'
COPY *.py ./
COPY scripts ./scripts
COPY database ./database
RUN mkdir -p /data/chroma
EXPOSE 8000 8501
CMD ["python", "-m", "uvicorn", "mains:app", "--host", "0.0.0.0", "--port", "8000"]
