# health

Union Blitz — a small browser game. Throw a ball at a moving target: each hit
scores 5 points and speeds the target up, each miss costs a life. Three lives,
and the high score is kept in `localStorage`.

## Running it

It is a single static page with no build step and no dependencies. Serve the
repository root with any static file server:

```bash
python3 -m http.server 8000
# then open http://localhost:8000
```

Opening `index.html` directly from the filesystem mostly works, but some
browsers block the audio files over `file://`.

## Layout

```
index.html        markup, styles and game loop
public/
  target.jpg      the target sprite
  hit.mp3         played on a hit
  miss.mp3        played on a miss
```

## Controls

Press **Start Game**, then swipe on the canvas to throw — the swipe direction
sets the throw angle. Touch events only, so it is built for a phone or a
touch-enabled screen.
