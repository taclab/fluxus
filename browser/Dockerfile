FROM mcr.microsoft.com/playwright/python:v1.65.1-noble
WORKDIR /srv
RUN pip install --no-cache-dir playwright==1.65.1 "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1" "pydantic>=2,<3"
COPY server.py ./
# Utilisateur non privilégié fourni par l'image Playwright.
USER pwuser
EXPOSE 3000
# ProxyHandler({}) : ignorer un éventuel proxy HTTP injecté dans l'environnement du conteneur.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request,sys; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); sys.exit(0 if o.open('http://127.0.0.1:3000/health', timeout=3).status == 200 else 1)"]
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "3000"]
