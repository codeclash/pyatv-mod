# Adding webserver with swagger 

aptv docker image:

```bash
docker run -itd --restart-unless-stopped --network=host -p:8080:8080 ghcr.io/postlund/pyatv:master sh
```


## install nano
```bash
apk update && apk add nano
```


## Add swagger module

```bash
python -m pip install aiohttp-swagger
```


## webserver.py
Save it as `/usr/local/bin/webserver.py`

```python

```


## Run it

```bash
python /usr/local/bin/webserver.py &
```
