# Field ASR image — kernel + FIXTURE_PACK only. Requires OPENROUTER_API_KEY at runtime.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY sr_agent ./sr_agent
COPY tests ./tests

RUN pip install --no-cache-dir -e ".[dev]"

ENV FIELD_ASR=1 \
    FIELD_ASR_N=20 \
    FIELD_ASR_BASELINE_KNOB=gate_off \
    OPENROUTER_MODEL=openai/gpt-5.6-luna \
    ANTHROPIC_API_KEY=dummy \
    SR_SECRET_KEY=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    PYTEST_ADDOPTS="-q -s --timeout=3600"

CMD ["pytest", "tests/security/field_mi_asr", "-m", "field_asr"]
