ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apk add --no-cache python3 py3-pip jq curl

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY bridge.py /app/bridge.py
COPY config.yaml /app/config.yaml
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
