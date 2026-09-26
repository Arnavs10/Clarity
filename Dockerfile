# The public demo, built for a Hugging Face Space (Docker SDK). Runs anywhere Docker does:
#   docker build -t clarity . && docker run -p 7860:7860 clarity
FROM python:3.12-slim

# Spaces run the container as user 1000, so everything the app writes to (the
# statute index is rebuilt from data/corpus on first start) must belong to it.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

# requirements-lock.txt is written by deploy/push_space.sh from the machine the
# release was tested on, so the Space runs the same versions. Without it, the
# unpinned list.
COPY --chown=user requirements*.txt ./
RUN pip install --no-cache-dir --user -r \
    $( [ -f requirements-lock.txt ] && echo requirements-lock.txt || echo requirements.txt )

COPY --chown=user . .

# Live audits, exactly as a local run. The key is a Space secret (GROQ_API_KEY),
# never part of the image.
ENV LLM_PROVIDER=groq \
    LLM_MODEL=openai/gpt-oss-120b

# Public mode with live audits on: one audit at a time, and version comparison
# (which stores the drafts it compares) switched off. The repo link lets a
# visitor run Clarity on their own machine.
ENV CLARITY_PUBLIC=1 \
    CLARITY_PUBLIC_LIVE=1 \
    CLARITY_REPO_URL=https://github.com/Arnavs10/clarity

EXPOSE 7860
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "7860"]
