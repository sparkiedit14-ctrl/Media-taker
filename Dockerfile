FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Requirements.txt ./Requirements.txt
RUN pip install --no-cache-dir -r Requirements.txt

COPY app.py ./app.py

# Set this to your deployed frontend URL(s), comma-separated, in production.
ENV FRONTEND_ORIGINS="*"

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
