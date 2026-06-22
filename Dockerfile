FROM alpine:3.20

# Optional build proxy. If your network cannot reach GitHub/Cloudflare during
# docker build, uncomment the following lines and replace the proxy address.
# ENV HTTP_PROXY=http://your-proxy-host:port \
#     HTTPS_PROXY=http://your-proxy-host:port

RUN apk add --no-cache python3 py3-pip py3-flask py3-yaml curl bash

WORKDIR /app

# Install cloudflared
RUN ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/') && \
    curl -sSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}" -o /usr/local/bin/cloudflared && \
    chmod +x /usr/local/bin/cloudflared

COPY app.py .
COPY templates/ templates/

# Setup data dir
RUN mkdir -p /root/.cf-tunnel-manager/bin && \
    ln -sf /usr/local/bin/cloudflared /root/.cf-tunnel-manager/bin/cloudflared

# Clear build-time proxy env for runtime (use host network)
ENV HTTP_PROXY= \
    HTTPS_PROXY=

EXPOSE 5000
CMD ["python3", "app.py"]
