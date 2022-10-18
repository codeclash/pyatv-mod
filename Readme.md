# Webserver to play sound over HomePod Mini or Sonos

This project is based on [pyatv](https://github.com/postlund/pyatv)

I've modified it to include a simple webserver with a method to allow playing a sound file on a homepod or sonos.

1. You need to find the `identifier` of the device first via `http://<server>:<port>/scan`
2. Then you can play the soud via: `http://<server>:<port>/playsound/<identifier>/<url-of-file>`
  * The `<url-of-file>` can either be /data/xy.mp3 or http(s)://url-to-xy.mp3


A swagger interface is available at: `http://<server>:<port>/api/doc`

Run the image:

```bash
docker run -itd --restart-unless-stopped --network=host --name atv-mode -e PORT=8081 -p 8081:8081 codeclash/pyatv-mod:0.0.1 
```


