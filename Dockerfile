FROM python:3.12-slim
WORKDIR /app
RUN adduser --disabled-password --gecos '' appuser

# Copy everything first, then install
COPY . .
RUN pip install --no-cache-dir .
RUN mkdir -p uploads && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000
CMD ["uvicorn", "finspark.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
