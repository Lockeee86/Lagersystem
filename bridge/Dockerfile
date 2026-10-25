FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /bridge
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY meshtastic_bridge.py .
RUN useradd --system --uid 1000 bridge
USER bridge
CMD ["python", "meshtastic_bridge.py"]
