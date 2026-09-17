FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Requirements.txt ./Requirements.txt
RUN pip install --no-cache-dir -r Requirements.txt

COPY app.py ./app.py
COPY Index.html ./Index.html

# Never COPY a cookie file into the image. Configure YOUTUBE_COOKIES_FILE
# in Render to point to a mounted Secret File instead.
ENV FRONTEND_ORIGINS="*"
ENV YOUTUBE_COOKIES_FILE=""

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
