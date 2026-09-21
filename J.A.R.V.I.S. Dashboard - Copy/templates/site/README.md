# {name}

A four-page static site. No build step, no dependencies.

`index.html` `work.html` `about.html` `contact.html`

There is no template engine, so the header and footer are repeated on each
page. That is the honest trade for having nothing to install: change the nav
in one file and change it in all four.

- `tailwind.css` — a compiled Tailwind utility subset, already linked. Generated, not authored; do not edit it.
  Colour is token-backed, so `bg-accent` and `text-ink` work and `bg-blue-500` does not.
- `style.css` — tokens, primitives, components, motion. Change `--accent`.
- `base.js` — scroll reveals and marking the current nav item.
