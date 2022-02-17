ARG         TAG
FROM        python:3.10-slim
WORKDIR     /app
ADD         . ./
RUN         /app/scripts/setup.sh
EXPOSE      8000
CMD         ["bash", "/app/scripts/start.sh"]
