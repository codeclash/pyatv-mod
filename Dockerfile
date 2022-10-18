FROM ghcr.io/postlund/pyatv:master

# update and install nano
RUN apk update && apk add nano

# install swagger module
RUN python -m pip install aiohttp-swagger

# copy webserver app
COPY ./webserver.py /usr/local/bin/

# create default assets folder
RUN mkdir -p /assets
COPY ./assets /assets

CMD python /usr/local/bin/webserver.py

# build it: 
# docker build -t codeclash/pyatv-mod:0.0.1 .

# run it prod:
# docker run -itd --network=host --name atv-mode -e PORT=8081 -p 8081:8081 codeclash/pyatv-mod:0.0.1 

# run it test:
# docker run -itd --name atv-mode -e PORT=8081 -p 8081:8081 codeclash/pyatv-mod:0.0.1

