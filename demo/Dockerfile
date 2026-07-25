# Home APM — self-contained demo image (Hugging Face Space / any Docker host).
# Stdlib-only: no pip install, no third-party deps. Serves on $PORT (7860 on HF).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /app

COPY app.py trace_reconstruct.py ./
COPY fixtures/ ./fixtures/

EXPOSE 7860
CMD ["python", "app.py"]
