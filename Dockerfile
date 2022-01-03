# The build-stage image:
FROM continuumio/miniconda3 AS build

# Install the package as normal:
COPY src/environment.yml .
RUN conda env create -f environment.yml

# Install conda-pack:
RUN conda install -c conda-forge conda-pack

# Use conda-pack to create a standalone enviornment
# in /venv:
RUN conda-pack -n vue -o /tmp/env.tar && \
  mkdir /venv && cd /venv && tar xf /tmp/env.tar && \
  rm /tmp/env.tar

# We've put venv in same path it'll be in final image,
# so now fix up paths:
RUN /venv/bin/conda-unpack


# The runtime-stage image; we can use Debian as the
# base image since the Conda env also includes Python
# for us.
FROM debian:buster AS runtime

ARG UID=1012
ARG GID=1012

RUN addgroup --gid $GID vuegraf
RUN adduser --gid $GID --uid $UID --home /opt/vuegraf vuegraf
# Copy /venv from the previous stage:
COPY --from=build /venv /venv

WORKDIR /opt/vuegraf
COPY src/vuegraf/*.py ./
# RUN  chmod a+x *.py
RUN  chmod 755 *.py
USER $UID

VOLUME /opt/vuegraf/conf

# When image is run, run the code with the environment
# activated:
SHELL ["/bin/bash", "-c"]

RUN source /venv/bin/activate && pip install pyemvue

# ENTRYPOINT ["/bin/bash"]
ENTRYPOINT source /venv/bin/activate && \
           python /opt/vuegraf/vuegraf.py /opt/vuegraf/conf/vuegraf.json
# CMD ["/opt/vuegraf/conf/vuegraf.json"]