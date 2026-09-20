FROM python:3.12-alpine
WORKDIR /app
COPY smart-optimizer-ui.py /app/smart-optimizer-ui.py
RUN mkdir -p /config /data
ENV SMART_UI_HOST=0.0.0.0 \
    SMART_UI_PORT=8788 \
    RADARR_OPTIMIZER_STATE=/data/radarr-state.json \
    SONARR_OPTIMIZER_STATE=/data/sonarr-state.json \
    SMART_OPTIMIZER_CONTROL=/config/smart-optimizer-control.json \
    SMART_OPTIMIZER_CONNECTIONS=/config/smart-optimizer-connections.json
EXPOSE 8788
VOLUME ["/config", "/data"]
CMD ["python3","/app/smart-optimizer-ui.py"]
