FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_LINK_MODE=copy
RUN useradd -m -u 1000 app
WORKDIR /home/app/bumpr
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=app:app . .
USER app
ENV BUMPR_WORKDIR=/tmp/bumpr-workspaces BUMPR_RUNS_DIR=/tmp/bumpr-runs BUMPR_WORKSPACE_PYTHON=3.12
EXPOSE 7860
CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "7860"]
