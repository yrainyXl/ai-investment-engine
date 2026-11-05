FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY investment_engine ./investment_engine
COPY fixtures ./fixtures
RUN pip install --no-cache-dir '.[api]'
USER nobody
EXPOSE 8018
# Preview API must be behind an authenticated internal reverse proxy if exposed off-box.
CMD ["uvicorn", "investment_engine.api:app", "--host", "0.0.0.0", "--port", "8018"]
