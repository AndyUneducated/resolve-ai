FROM python:3.12-slim

WORKDIR /app

# Install each MCP server package so `python -m mcp_servers.<name>` works.
COPY packages/mcp-servers/stripe /app/packages/mcp-servers/stripe
COPY packages/mcp-servers/zendesk /app/packages/mcp-servers/zendesk
COPY packages/mcp-servers/slack /app/packages/mcp-servers/slack
COPY packages/mcp-servers/salesforce /app/packages/mcp-servers/salesforce
COPY packages/mcp-servers/intercom /app/packages/mcp-servers/intercom

RUN pip install --no-cache-dir \
    /app/packages/mcp-servers/stripe \
    /app/packages/mcp-servers/zendesk \
    /app/packages/mcp-servers/slack \
    /app/packages/mcp-servers/salesforce \
    /app/packages/mcp-servers/intercom

ENTRYPOINT ["python"]
