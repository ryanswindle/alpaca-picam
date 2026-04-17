FROM python:3.12-slim

LABEL maintainer="Ryan Swindle <rswindle@gmail.com>"
LABEL description="ASCOM Alpaca server for Teledyne cameras (PICam)"

# eBUS SDK userspace runtime dependencies
RUN apt-get update && apt-get install -y \
    libstdc++6 \
    libgomp1 \
    libudev1 \
    && rm -rf /var/lib/apt/lists/*

# --- PICam + eBUS runtime libraries ---
# These must be obtained from the Teledyne Linux SDK tarball and
# extracted locally BEFORE building this image. They cannot be
# downloaded at build time (auth-gated). Expected layout:
#
#   docker-context/
#     picam-runtime/        ← contents of PICam Linux runtime tarball
#     ebus-runtime/         ← contents of eBUS SDK Runtime (not full install)
#
# Copy PICam shared libraries
COPY picam-runtime/lib/*.so* /usr/local/lib/picam/

# Copy eBUS SDK userspace runtime (needed for PCIe frame grabber)
COPY ebus-runtime/lib/*.so* /usr/local/lib/ebus/

# Add both to the dynamic linker search path
RUN echo "/usr/local/lib/picam" >> /etc/ld.so.conf.d/picam.conf && \
    echo "/usr/local/lib/ebus"  >> /etc/ld.so.conf.d/ebus.conf  && \
    ldconfig

WORKDIR /alpyca

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY config.yaml .
COPY *.py ./

CMD ["python", "main.py"]