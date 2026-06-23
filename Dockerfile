FROM python:3.12-slim

WORKDIR /app

# Copy requirements first so this layer is cached unless deps change
COPY requirements.txt ./

# Install all dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and Streamlit config
COPY streamlit_app.py epic_chatbot.py EPIC_Server.py EPIC_Client.py ./
COPY .streamlit/config.toml .streamlit/config.toml

EXPOSE 8501

# ANTHROPIC_API_KEY must be supplied at runtime — never bake it into the image.
# Pass it with: docker run --env-file .env ...
#           or: docker run -e ANTHROPIC_API_KEY=sk-ant-... ...

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "streamlit_app.py", \
            "--server.port=8501", "--server.address=0.0.0.0", \
            "--server.headless=true"]
