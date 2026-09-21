# {name}

A working MVP: a front end, a JSON API, and a database. Standard library
only — no pip install, no node, no build step.

## Run it

```bash
python server.py
```

Then open http://localhost:8000.

## Check it

```bash
python main.py
```

`main.py` exercises the storage layer against a throwaway database and exits.
It is what the build check runs, which is why it must never serve.

## Files

| | |
|---|---|
| `index.html` | the interface |
| `app.js` | data layer and UI wiring |
| `base.js` | scroll reveals, nav marking |
| `style.css` | design tokens, primitives, components, motion |
| `server.py` | the API and the static server |
| `main.py` | smoke test |
| `schema.sql` | the tables |

## The API

| | |
|---|---|
| `GET /api/items` | list |
| `POST /api/items` | `{"title": "..."}` |
| `PATCH /api/items/<id>` | `{"done": true}` |
| `DELETE /api/items/<id>` | remove |

## A note on the preview pane

The dashboard preview serves these files statically, so `/api/*` is not
reachable from it. `app.js` notices and falls back to `localStorage`, which
means the interface is fully usable in the preview — it just is not talking to
the database. Run `python server.py` for the real thing.

`tailwind.css` is a compiled Tailwind utility subset, already linked.
Generated, not authored - do not edit it. Colour is token-backed, so
`bg-accent` and `text-ink` work and `bg-blue-500` does not.
