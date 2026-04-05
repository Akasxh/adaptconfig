FROM python:3.12-slim
WORKDIR /app

# Copy and install dependencies
COPY . .
RUN pip install --no-cache-dir .
RUN mkdir -p uploads data

EXPOSE 8000
CMD ["uvicorn", "finspark.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
