# {name}

A single-page site. Open `index.html`, or use the preview pane.

- `tailwind.css` — a compiled Tailwind utility subset, already linked. Generated, not authored; do not edit it.
  Colour is token-backed, so `bg-accent` and `text-ink` work and `bg-blue-500` does not.
- `style.css` — the design foundation: tokens, primitives, components, motion.
  Change `--accent` at the top and the whole page follows.
- `base.js` — reveal-on-scroll and nav marking. Nothing else.

Everything is plain HTML and CSS. No build step, no dependencies.
