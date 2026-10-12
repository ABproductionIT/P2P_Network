FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY signaling_server.py .
COPY docs ./docs
USER app
EXPOSE 9000
CMD ["python", "signaling_server.py"]
