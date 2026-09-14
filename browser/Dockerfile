FROM mcr.microsoft.com/playwright/python:v1.47.0-noble
WORKDIR /srv
RUN pip install --no-cache-dir playwright==1.47.0 fastapi uvicorn[standard] pydantic
COPY server.py ./
EXPOSE 3000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "3000"]
