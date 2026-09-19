FROM python:3.12-alpine
WORKDIR /app
COPY smart-optimizer-ui.py /app/smart-optimizer-ui.py
EXPOSE 8788
CMD ["python3","/app/smart-optimizer-ui.py"]
