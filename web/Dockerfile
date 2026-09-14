FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

VOLUME /data

EXPOSE 8080

CMD ["./entrypoint.sh"]
