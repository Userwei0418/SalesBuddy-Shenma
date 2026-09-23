FROM langgenius/dify-web@sha256:6a81fcdd82b51f6fe3436cdc25d1a358cd301a2c3457554bf96de7f5f32a6b5a
USER root
RUN rm -rf /app/targets
ADD --chown=dify:dify web-code.tgz /app/
USER dify
