FROM python:3.15-slim

WORKDIR /app
COPY pipeline/requirements.txt ./pipeline/requirements.txt
RUN pip install --no-cache-dir -r pipeline/requirements.txt
COPY pipeline/ ./pipeline/

ENTRYPOINT ["python", "-m"]

